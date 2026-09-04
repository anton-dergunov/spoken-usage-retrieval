#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from speech_retrieval.captions import manual_units, segment_payload
from speech_retrieval.text import join_text


DEFAULT_FOCUS_TERMS = (
    "hubiera",
    "acaba de",
    "diría",
    "qué fuerte",
    "y simplemente",
    "curiosidad",
)


def event_start_times(payload: dict[str, Any]) -> set[int]:
    return {
        int(event["tStartMs"])
        for event in payload.get("events") or []
        if event.get("tStartMs") is not None and event.get("segs")
    }


def sample_indices(length: int, count: int) -> list[int]:
    if length <= 0:
        return []
    count = min(length, max(1, count))
    if count == 1:
        return [0]
    return sorted({round(index * (length - 1) / (count - 1)) for index in range(count)})


def english_for_interval(units: list, start: float, end: float) -> str:
    text = ""
    for unit in units:
        if start - 0.001 <= unit.start < end - 0.001:
            text = join_text(text, unit.text)
    return text


def find_authored_track(tracks_dir: Path, video_id: str) -> Path | None:
    matches = sorted(tracks_dir.glob(f"{video_id}.manual.*.json3"))
    return matches[0] if matches else None


def compare(
    *,
    data_dir: Path,
    tracks_dir: Path,
    output_path: Path,
    per_video: int,
    focus_terms: tuple[str, ...],
) -> dict[str, Any]:
    raw_root = data_dir / "raw" / "videos"
    results: list[dict[str, Any]] = []
    missing_tracks: list[str] = []

    for metadata_path in sorted(raw_root.glob("*/metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        video_id = metadata["video_id"]
        track_path = find_authored_track(tracks_dir, video_id)
        if track_path is None:
            if metadata.get("caption_kind") == "manual":
                missing_tracks.append(video_id)
            continue

        spanish_path = metadata_path.parent / "subtitles.raw.json3"
        spanish_payload = json.loads(spanish_path.read_text(encoding="utf-8"))
        english_payload = json.loads(track_path.read_text(encoding="utf-8"))
        utterances = segment_payload(
            spanish_payload,
            video_id=video_id,
            caption_kind=metadata["caption_kind"],
            video_duration=metadata.get("duration"),
        )
        english_units = manual_units(english_payload)
        selected_indices = set(sample_indices(len(utterances), per_video))
        selected_indices.update(
            index
            for index, utterance in enumerate(utterances)
            if any(term.casefold() in utterance.text.casefold() for term in focus_terms)
        )
        pairs = [
            {
                "sample_kind": "focus" if any(
                    term.casefold() in utterances[index].text.casefold() for term in focus_terms
                ) else "regular",
                "start": utterances[index].start,
                "end": utterances[index].end,
                "spanish": utterances[index].text,
                "english": english_for_interval(
                    english_units, utterances[index].start, utterances[index].end
                ),
            }
            for index in sorted(selected_indices)
        ]
        spanish_starts = event_start_times(spanish_payload)
        english_starts = event_start_times(english_payload)
        results.append(
            {
                "video_id": video_id,
                "title": metadata["title"],
                "spanish_cues": len(spanish_starts),
                "english_cues": len(english_starts),
                "equal_start_timestamps": len(spanish_starts & english_starts),
                "utterance_count": len(utterances),
                "pairs": pairs,
            }
        )

    if not results:
        raise ValueError(
            f"no authored English tracks found under {tracks_dir}; run the coverage experiment first"
        )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": {
            "regular_samples_per_video": per_video,
            "focus_terms": list(focus_terms),
            "english_assignment": "English cues whose start time falls inside the Spanish utterance",
        },
        "missing_authored_tracks": missing_tracks,
        "videos": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare timestamp-aligned samples for reviewing authored English literalness."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--tracks-dir",
        type=Path,
        default=Path("data/experiments/english-track-download-coverage/tracks"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/experiments/authored-english-literalness/comparison.json"),
    )
    parser.add_argument("--per-video", type=int, default=11)
    parser.add_argument(
        "--focus-term",
        action="append",
        dest="focus_terms",
        help="Case-insensitive Spanish term to include in addition to regular samples",
    )
    args = parser.parse_args()
    if args.per_video < 1:
        parser.error("--per-video must be at least 1")
    focus_terms = tuple(args.focus_terms) if args.focus_terms else DEFAULT_FOCUS_TERMS
    print(
        json.dumps(
            compare(
                data_dir=args.data_dir,
                tracks_dir=args.tracks_dir,
                output_path=args.output,
                per_video=args.per_video,
                focus_terms=focus_terms,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
