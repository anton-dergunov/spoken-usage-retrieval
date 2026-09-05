from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .identity import segment_id
from .models import Segment, TimedTextSegment, TimedUnit
from .text import TERMINAL_RE, clean_spacing, join_text, normalize_token, tokens_with_spans

ANNOTATION_RE = re.compile(
    r"\[(?:m[uú]sica|aplausos?|risas?|silencio|inaudible)[^]]*\]", re.IGNORECASE
)
TAG_RE = re.compile(r"<[^>]+>")


def _clean_caption(value: str) -> str:
    value = TAG_RE.sub("", value).replace("\n", " ")
    value = ANNOTATION_RE.sub("", value)
    return clean_spacing(value)


def _events(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for event in payload.get("events", []):
        if event.get("segs") and event.get("tStartMs") is not None:
            yield event


def manual_units(payload: dict[str, Any]) -> list[TimedUnit]:
    units: list[TimedUnit] = []
    for event in _events(payload):
        text = _clean_caption("".join(str(seg.get("utf8", "")) for seg in event["segs"]))
        if not text:
            continue
        start = float(event["tStartMs"]) / 1000
        end = start + max(float(event.get("dDurationMs", 0)) / 1000, 0.08)
        units.append(TimedUnit(text=text, start=start, end=end))
    return _deduplicate_units(units)


def automatic_units(payload: dict[str, Any]) -> list[TimedUnit]:
    candidates: list[TimedUnit] = []
    for event in _events(payload):
        event_start = float(event["tStartMs"]) / 1000
        event_end = event_start + max(float(event.get("dDurationMs", 0)) / 1000, 0.08)
        for seg in event["segs"]:
            raw = str(seg.get("utf8", ""))
            if not raw.strip() or raw.strip() == "\n":
                continue
            text = _clean_caption(raw)
            if not text:
                continue
            start = event_start + float(seg.get("tOffsetMs", 0)) / 1000
            candidates.append(TimedUnit(text=text, start=start, end=event_end))

    candidates = _deduplicate_units(sorted(candidates, key=lambda unit: (unit.start, unit.end)))
    units: list[TimedUnit] = []
    for index, unit in enumerate(candidates):
        next_start = candidates[index + 1].start if index + 1 < len(candidates) else None
        estimated_end = min(unit.end, unit.start + 0.4)
        if next_start is not None and next_start > unit.start:
            estimated_end = min(estimated_end, next_start)
        units.append(replace(unit, end=max(unit.start + 0.08, estimated_end)))
    return units


def _deduplicate_units(units: list[TimedUnit]) -> list[TimedUnit]:
    seen: set[tuple[int, str]] = set()
    result: list[TimedUnit] = []
    for unit in units:
        key = (round(unit.start * 100), normalize_token(unit.text))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(unit)
    return result


def _segment_from_units(
    video_id: str,
    video_key: str,
    source_language: str,
    track_id: str,
    units: list[TimedUnit],
    boundary_reason: str,
    video_duration: float | None,
    caption_kind: str,
) -> Segment:
    text = ""
    timed_segments: list[TimedTextSegment] = []
    for unit in units:
        next_text = join_text(text, unit.text)
        unit_start = len(text)
        while unit_start < len(next_text) and next_text[unit_start].isspace():
            unit_start += 1
        timed_segments.append(
            TimedTextSegment(
                text=next_text[unit_start:],
                start=round(unit.start, 3),
                end=round(unit.end, 3),
                char_start=unit_start,
                char_end=len(next_text),
            )
        )
        text = next_text
    text = clean_spacing(text)
    start = units[0].start
    end = max(units[-1].end, start + 0.08)
    token_count = len(tokens_with_spans(text))
    confidence = {"punctuation": 1.0, "pause": 0.78, "end": 0.68, "forced": 0.38}.get(
        boundary_reason, 0.5
    )
    duration = end - start
    duration_score = 1.0 if 2 <= duration <= 12 else max(0.0, 1 - abs(duration - 7) / 12)
    length_score = 1.0 if 5 <= token_count <= 25 else max(0.0, 1 - abs(token_count - 15) / 24)
    source_score = 1.0 if caption_kind == "manual" else 0.65
    quality = round(
        0.4 * confidence + 0.25 * duration_score + 0.2 * length_score + 0.15 * source_score, 4
    )
    clip_start = max(0.0, start - 0.35)
    clip_end = end + 0.65
    if video_duration and video_duration > 0:
        clip_end = min(video_duration, clip_end)
    if caption_kind == "manual":
        timed_segments = [
            TimedTextSegment(
                text=text,
                start=round(start, 3),
                end=round(end, 3),
                char_start=0,
                char_end=len(text),
            )
        ]
    return Segment(
        id=segment_id(
            provider_video_id=video_id,
            source_language=source_language,
            track=track_id,
            start=start,
            end=end,
            text=text,
        ),
        video_key=video_key,
        video_id=video_id,
        source_language=source_language,
        track_id=track_id,
        text=text,
        start=round(start, 3),
        end=round(end, 3),
        clip_start=round(clip_start, 3),
        clip_end=round(max(clip_start + 0.1, clip_end), 3),
        boundary_reason=boundary_reason,
        boundary_confidence=confidence,
        quality_score=quality,
        token_count=token_count,
        segments=tuple(timed_segments),
    )


def _merge_short_segments(
    video_id: str,
    groups: list[tuple[list[TimedUnit], str]],
    video_duration: float | None,
    caption_kind: str,
    hard_seconds: float,
    hard_tokens: int,
) -> list[tuple[list[TimedUnit], str]]:
    merged: list[tuple[list[TimedUnit], str]] = []
    index = 0
    while index < len(groups):
        units, reason = groups[index]
        count = sum(len(tokens_with_spans(unit.text)) for unit in units)
        if count < 4 and reason != "punctuation" and index + 1 < len(groups):
            following, following_reason = groups[index + 1]
            combined = units + following
            combined_count = sum(len(tokens_with_spans(unit.text)) for unit in combined)
            if (
                combined[-1].end - combined[0].start <= hard_seconds
                and combined_count <= hard_tokens
            ):
                merged.append((combined, following_reason))
                index += 2
                continue
        if count < 4 and merged and merged[-1][1] != "punctuation":
            previous, previous_reason = merged[-1]
            combined = previous + units
            combined_count = sum(len(tokens_with_spans(unit.text)) for unit in combined)
            if (
                combined[-1].end - combined[0].start <= hard_seconds
                and combined_count <= hard_tokens
            ):
                merged[-1] = (combined, reason if reason != "end" else previous_reason)
                index += 1
                continue
        merged.append((units, reason))
        index += 1
    return merged


def segment_payload(
    payload: dict[str, Any],
    *,
    video_id: str,
    video_key: str | None = None,
    source_language: str = "und",
    track_id: str = "unknown",
    caption_kind: str,
    video_duration: float | None = None,
    pause_seconds: float = 0.9,
    hard_seconds: float = 15.0,
    hard_tokens: int = 32,
) -> list[Segment]:
    units = manual_units(payload) if caption_kind == "manual" else automatic_units(payload)
    if not units:
        return []

    groups: list[tuple[list[TimedUnit], str]] = []
    current: list[TimedUnit] = []
    for index, unit in enumerate(units):
        current.append(unit)
        text = ""
        for item in current:
            text = join_text(text, item.text)
        token_count = len(tokens_with_spans(text))
        duration = current[-1].end - current[0].start
        next_unit = units[index + 1] if index + 1 < len(units) else None
        reason: str | None = None
        if TERMINAL_RE.search(text):
            reason = "punctuation"
        elif next_unit is None:
            reason = "end"
        elif next_unit.start - current[-1].end >= pause_seconds:
            reason = "pause"
        elif duration >= hard_seconds or token_count >= hard_tokens:
            reason = "forced"
        if reason:
            groups.append((current, reason))
            current = []

    groups = _merge_short_segments(
        video_id, groups, video_duration, caption_kind, hard_seconds, hard_tokens
    )
    segments = [
        _segment_from_units(
            video_id,
            video_key or video_id,
            source_language,
            track_id,
            group,
            reason,
            video_duration,
            caption_kind,
        )
        for group, reason in groups
    ]
    return [segment for segment in segments if segment.token_count > 0 and segment.text]


def segments_from_files(metadata_path: Path, captions_path: Path) -> list[Segment]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload = json.loads(captions_path.read_text(encoding="utf-8"))
    return segment_payload(
        payload,
        video_id=metadata["video_id"],
        video_key=metadata["video_key"],
        source_language=metadata["source_language"],
        track_id=metadata["track_id"],
        caption_kind=metadata["caption_kind"],
        video_duration=metadata.get("duration"),
    )
