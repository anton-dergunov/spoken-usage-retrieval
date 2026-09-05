from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalogue import Catalogue, Channel, canonical_language, load_catalogue
from .identity import (
    ANALYZER_ID,
    CACHE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    track_id,
    video_key,
)


class AcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptionTrack:
    kind: str
    language: str


Runner = Callable[[Sequence[str]], str]


def _run_ytdlp(arguments: Sequence[str]) -> str:
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", *arguments]
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
    if result.returncode:
        message = (
            result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "yt-dlp failed"
        )
        raise AcquisitionError(message)
    return result.stdout


def _json_output(arguments: Sequence[str], runner: Runner) -> dict[str, Any]:
    output = runner([*arguments, "--dump-single-json", "--no-warnings"])
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise AcquisitionError("yt-dlp returned invalid JSON") from error


def _provider_language(value: str) -> str | None:
    candidate = value.removesuffix("-orig")
    try:
        return canonical_language(candidate)
    except ValueError:
        return None


def _matching_track_keys(tracks: dict[str, Any], source_language: str) -> list[str]:
    source_primary = source_language.split("-", 1)[0]
    matches: list[tuple[tuple[int, int, str], str]] = []
    for key in tracks:
        language = _provider_language(key)
        if language is None or language.split("-", 1)[0] != source_primary:
            continue
        priority = (
            0 if language == source_language else 1,
            0 if key.endswith("-orig") else 1,
            key,
        )
        matches.append((priority, key))
    return [key for _, key in sorted(matches)]


def select_caption_track(info: dict[str, Any], source_language: str) -> CaptionTrack | None:
    source_language = canonical_language(source_language)
    manual_keys = _matching_track_keys(info.get("subtitles") or {}, source_language)
    if manual_keys:
        return CaptionTrack("manual", manual_keys[0])
    automatic_keys = _matching_track_keys(info.get("automatic_captions") or {}, source_language)
    if automatic_keys:
        return CaptionTrack("automatic", automatic_keys[0])
    return None


def _valid_cache(track_dir: Path, *, source_language: str, expected_video_key: str) -> bool:
    try:
        metadata = json.loads((track_dir / "metadata.json").read_text(encoding="utf-8"))
        caption_bytes = (track_dir / "subtitles.raw.json3").read_bytes()
        captions = json.loads(caption_bytes)
        return bool(
            metadata.get("cache_schema_version") == CACHE_SCHEMA_VERSION
            and metadata.get("source_language") == source_language
            and metadata.get("video_key") == expected_video_key
            and metadata.get("track_id") == track_dir.name
            and metadata.get("content_sha256") == hashlib.sha256(caption_bytes).hexdigest()
            and captions.get("events")
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _cached_transcript(
    video_dir: Path, *, source_language: str, expected_video_key: str
) -> dict[str, Any] | None:
    if not video_dir.exists():
        return None
    for candidate in sorted(path for path in video_dir.iterdir() if path.is_dir()):
        if _valid_cache(
            candidate,
            source_language=source_language,
            expected_video_key=expected_video_key,
        ):
            return json.loads((candidate / "metadata.json").read_text(encoding="utf-8"))
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def discover_channel(channel: Channel, scan_limit: int, runner: Runner) -> list[dict[str, Any]]:
    info = _json_output(["--flat-playlist", "--playlist-end", str(scan_limit), channel.url], runner)
    candidates: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        candidates.append(
            {
                "id": entry["id"],
                "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
                "title": entry.get("title") or entry["id"],
                "duration": entry.get("duration"),
                "live_status": entry.get("live_status"),
            }
        )
    return candidates


def _download_one(
    candidate: dict[str, Any],
    channel: Channel,
    catalogue: Catalogue,
    raw_root: Path,
    runner: Runner,
) -> dict[str, Any]:
    provider = "youtube"
    provider_video_id = candidate["id"]
    stable_video_key = video_key(provider, catalogue.language, provider_video_id)
    video_dir = raw_root / stable_video_key
    cached = _cached_transcript(
        video_dir,
        source_language=catalogue.language,
        expected_video_key=stable_video_key,
    )
    if cached is not None:
        return {"video_id": provider_video_id, "status": "cached", "metadata": cached}

    info = _json_output(["--skip-download", candidate["url"]], runner)
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise AcquisitionError("video is live or upcoming")
    duration = info.get("duration")
    if duration is not None and float(duration) < 60:
        raise AcquisitionError("video is shorter than 60 seconds")
    track = select_caption_track(info, catalogue.language)
    if not track:
        raise AcquisitionError(f"no captions matching source language {catalogue.language}")

    stable_track_id = track_id(stable_video_key, track.kind, track.language)
    target = video_dir / stable_track_id
    raw_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{stable_track_id}-", dir=raw_root))
    try:
        output_template = temporary_dir / "caption.%(ext)s"
        subtitle_flag = "--write-subs" if track.kind == "manual" else "--write-auto-subs"
        runner(
            [
                "--skip-download",
                subtitle_flag,
                "--sub-langs",
                track.language,
                "--sub-format",
                "json3",
                "--no-warnings",
                "-o",
                str(output_template),
                candidate["url"],
            ]
        )
        caption_files = sorted(temporary_dir.glob("caption.*.json3"))
        if not caption_files:
            caption_files = sorted(temporary_dir.glob("caption.json3"))
        if not caption_files:
            raise AcquisitionError("caption download produced no json3 file")
        caption_payload = json.loads(caption_files[0].read_text(encoding="utf-8"))
        if not caption_payload.get("events"):
            raise AcquisitionError("caption file contains no events")
        caption_bytes = json.dumps(
            caption_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        (temporary_dir / "subtitles.raw.json3").write_bytes(caption_bytes)
        caption_files[0].unlink()
        metadata = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "catalogue_schema_version": catalogue.schema_version,
            "catalogue_id": catalogue.language,
            "source_language": catalogue.language,
            "provider": provider,
            "video_key": stable_video_key,
            "video_id": provider_video_id,
            "track_id": stable_track_id,
            "url": info.get("webpage_url") or candidate["url"],
            "title": info.get("title") or candidate["title"],
            "channel_id": info.get("channel_id"),
            "channel": info.get("channel") or channel.name,
            "channel_config_id": channel.id,
            "duration": duration,
            "upload_date": info.get("upload_date"),
            "thumbnail": info.get("thumbnail"),
            "varieties": list(channel.varieties or ()),
            "speech_style": list(channel.speech_style or ()),
            "caption_kind": track.kind,
            "caption_language": track.language,
            "content_sha256": hashlib.sha256(caption_bytes).hexdigest(),
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        (temporary_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        temporary_dir.replace(target)
        return {"video_id": provider_video_id, "status": "downloaded", "metadata": metadata}
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def acquire(
    *,
    config_path: Path,
    data_dir: Path,
    limit: int = 10,
    scan_limit: int = 25,
    runner: Runner = _run_ytdlp,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    catalogue = load_catalogue(config_path)
    channels = catalogue.enabled_channels
    if not channels:
        raise ValueError("channel catalogue contains no enabled channels")

    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "catalogue_schema_version": catalogue.schema_version,
        "catalogue_id": catalogue.language,
        "source_language": catalogue.language,
        "analyzer_id": ANALYZER_ID,
        "started_at": datetime.now(UTC).isoformat(),
        "requested": limit,
        "scan_limit_per_channel": scan_limit,
        "channels": [channel.id for channel in channels],
        "videos": [],
        "failures": [],
    }
    queues: list[list[dict[str, Any]]] = []
    for channel in channels:
        try:
            queues.append(discover_channel(channel, scan_limit, runner))
        except Exception as error:
            queues.append([])
            report["failures"].append(
                {"channel": channel.id, "stage": "discovery", "error": str(error)}
            )

    positions = [0] * len(channels)
    raw_root = data_dir / "raw" / "corpora" / catalogue.language
    while len(report["videos"]) < limit:
        attempted = False
        for channel_index, channel in enumerate(channels):
            if len(report["videos"]) >= limit:
                break
            position = positions[channel_index]
            if position >= len(queues[channel_index]):
                continue
            attempted = True
            candidate = queues[channel_index][position]
            positions[channel_index] += 1
            if candidate.get("live_status") in {"is_live", "is_upcoming"}:
                report["failures"].append(
                    {"video_id": candidate["id"], "channel": channel.id, "error": "live video"}
                )
                continue
            if candidate.get("duration") is not None and float(candidate["duration"]) < 60:
                report["failures"].append(
                    {"video_id": candidate["id"], "channel": channel.id, "error": "short video"}
                )
                continue
            try:
                result = _download_one(candidate, channel, catalogue, raw_root, runner)
                metadata = result["metadata"]
                report["videos"].append(
                    {
                        "video_key": metadata["video_key"],
                        "video_id": result["video_id"],
                        "track_id": metadata["track_id"],
                        "channel": channel.id,
                        "status": result["status"],
                        "source_language": metadata["source_language"],
                        "caption_kind": metadata["caption_kind"],
                        "caption_language": metadata["caption_language"],
                        "title": metadata["title"],
                    }
                )
            except Exception as error:
                report["failures"].append(
                    {"video_id": candidate["id"], "channel": channel.id, "error": str(error)}
                )
        if not attempted:
            break

    report["completed_at"] = datetime.now(UTC).isoformat()
    report["successful"] = len(report["videos"])
    report["complete"] = len(report["videos"]) == limit
    _write_json(data_dir / "reports" / f"acquisition-{catalogue.language}.json", report)
    return report
