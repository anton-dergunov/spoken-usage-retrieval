from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence


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
        message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "yt-dlp failed"
        raise AcquisitionError(message)
    return result.stdout


def _json_output(arguments: Sequence[str], runner: Runner) -> dict[str, Any]:
    output = runner([*arguments, "--dump-single-json", "--no-warnings"])
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise AcquisitionError("yt-dlp returned invalid JSON") from error


def _spanish_keys(tracks: dict[str, Any], preferred: Sequence[str]) -> list[str]:
    keys = [key for key in tracks if key == "es" or key.startswith("es-")]
    priority = {key: index for index, key in enumerate(preferred)}
    return sorted(keys, key=lambda key: (priority.get(key, len(priority)), key))


def select_caption_track(info: dict[str, Any]) -> CaptionTrack | None:
    manual = info.get("subtitles") or {}
    manual_keys = _spanish_keys(manual, ("es", "es-ES", "es-US", "es-419"))
    if manual_keys:
        return CaptionTrack("manual", manual_keys[0])
    automatic = info.get("automatic_captions") or {}
    automatic_keys = _spanish_keys(automatic, ("es-orig", "es", "es-ES", "es-419"))
    if automatic_keys:
        return CaptionTrack("automatic", automatic_keys[0])
    return None


def _valid_cache(video_dir: Path) -> bool:
    try:
        metadata = json.loads((video_dir / "metadata.json").read_text(encoding="utf-8"))
        captions = json.loads((video_dir / "subtitles.raw.json3").read_text(encoding="utf-8"))
        return bool(metadata.get("video_id") and captions.get("events"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def discover_channel(channel: dict[str, Any], scan_limit: int, runner: Runner) -> list[dict[str, Any]]:
    info = _json_output(
        ["--flat-playlist", "--playlist-end", str(scan_limit), channel["url"]], runner
    )
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
    channel: dict[str, Any],
    raw_root: Path,
    runner: Runner,
) -> dict[str, Any]:
    video_id = candidate["id"]
    target = raw_root / video_id
    if _valid_cache(target):
        metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        return {"video_id": video_id, "status": "cached", "metadata": metadata}

    info = _json_output(["--skip-download", candidate["url"]], runner)
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise AcquisitionError("video is live or upcoming")
    duration = info.get("duration")
    if duration is not None and float(duration) < 60:
        raise AcquisitionError("video is shorter than 60 seconds")
    track = select_caption_track(info)
    if not track:
        raise AcquisitionError("no Spanish captions")

    raw_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{video_id}-", dir=raw_root))
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
        (temporary_dir / "subtitles.raw.json3").write_text(
            json.dumps(caption_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        caption_files[0].unlink()
        metadata = {
            "video_id": video_id,
            "provider": "youtube",
            "url": info.get("webpage_url") or candidate["url"],
            "title": info.get("title") or candidate["title"],
            "channel_id": info.get("channel_id"),
            "channel": info.get("channel") or channel["name"],
            "channel_config_id": channel["id"],
            "duration": duration,
            "upload_date": info.get("upload_date"),
            "thumbnail": info.get("thumbnail"),
            "varieties": channel.get("varieties", []),
            "speech_style": channel.get("speech_style", []),
            "caption_kind": track.kind,
            "caption_language": track.language,
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        (temporary_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if target.exists():
            shutil.rmtree(target)
        temporary_dir.replace(target)
        return {"video_id": video_id, "status": "downloaded", "metadata": metadata}
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
    config = json.loads(config_path.read_text(encoding="utf-8"))
    channels = config.get("channels") or []
    if not channels:
        raise ValueError("channel configuration contains no channels")

    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "requested": limit,
        "scan_limit_per_channel": scan_limit,
        "channels": [channel["id"] for channel in channels],
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
                {"channel": channel["id"], "stage": "discovery", "error": str(error)}
            )

    positions = [0] * len(channels)
    raw_root = data_dir / "raw" / "videos"
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
                    {"video_id": candidate["id"], "channel": channel["id"], "error": "live video"}
                )
                continue
            if candidate.get("duration") is not None and float(candidate["duration"]) < 60:
                report["failures"].append(
                    {"video_id": candidate["id"], "channel": channel["id"], "error": "short video"}
                )
                continue
            try:
                result = _download_one(candidate, channel, raw_root, runner)
                report["videos"].append(
                    {
                        "video_id": result["video_id"],
                        "channel": channel["id"],
                        "status": result["status"],
                        "caption_kind": result["metadata"]["caption_kind"],
                        "caption_language": result["metadata"]["caption_language"],
                        "title": result["metadata"]["title"],
                    }
                )
            except Exception as error:
                report["failures"].append(
                    {"video_id": candidate["id"], "channel": channel["id"], "error": str(error)}
                )
        if not attempted:
            break

    report["completed_at"] = datetime.now(UTC).isoformat()
    report["successful"] = len(report["videos"])
    report["complete"] = len(report["videos"]) == limit
    _write_json(data_dir / "reports" / "acquisition.json", report)
    return report
