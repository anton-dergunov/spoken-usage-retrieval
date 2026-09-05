#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def run_ytdlp(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--ignore-config", *arguments],
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def english_keys(tracks: dict[str, Any]) -> list[str]:
    keys = [key for key in tracks if key == "en" or key.startswith("en-")]
    priority = {"en": 0, "en-US": 1, "en-GB": 2, "en-US-orig": 3}
    return sorted(keys, key=lambda key: (priority.get(key, 10), key))


def error_text(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return lines[-1] if lines else f"yt-dlp exited with {result.returncode}"


def valid_json3(path: Path) -> tuple[bool, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, 0
    event_count = len(payload.get("events") or [])
    return event_count > 0, event_count


def probe(*, data_dir: Path, output_dir: Path, delay: float) -> dict[str, Any]:
    raw_root = data_dir / "raw" / "videos"
    metadata_paths = sorted(raw_root.glob("*/metadata.json"))
    if not metadata_paths:
        raise ValueError(f"no cached video metadata found under {raw_root}")

    tracks_dir = output_dir / "tracks"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "data_dir": str(data_dir),
        "videos": [],
    }

    for metadata_path in metadata_paths:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        item: dict[str, Any] = {
            "video_id": metadata["video_id"],
            "title": metadata["title"],
            "source_caption_kind": metadata["caption_kind"],
        }
        info_result = run_ytdlp(
            ["--skip-download", "--dump-single-json", "--no-warnings", metadata["url"]]
        )
        if info_result.returncode:
            item.update({"info_status": "failed", "error": error_text(info_result)})
            report["videos"].append(item)
            time.sleep(delay)
            continue

        info = json.loads(info_result.stdout)
        manual_keys = english_keys(info.get("subtitles") or {})
        automatic_keys = english_keys(info.get("automatic_captions") or {})
        item.update(
            {
                "info_status": "ok",
                "manual_english_tracks": manual_keys,
                "automatic_english_tracks": automatic_keys,
            }
        )
        kind = "manual" if manual_keys else "automatic" if automatic_keys else None
        available_keys = manual_keys or automatic_keys
        language = available_keys[0] if available_keys else None
        if kind is None or language is None:
            item["download_status"] = "no_track"
            report["videos"].append(item)
            time.sleep(delay)
            continue

        destination = tracks_dir / f"{metadata['video_id']}.{kind}.{language}.json3"
        cached, cached_events = valid_json3(destination)
        if cached:
            item.update(
                {
                    "selected_kind": kind,
                    "selected_language": language,
                    "download_status": "cached",
                    "event_count": cached_events,
                    "track_path": str(destination),
                }
            )
            report["videos"].append(item)
            continue

        with tempfile.TemporaryDirectory(prefix=f"english-{metadata['video_id']}-") as directory:
            temporary_dir = Path(directory)
            subtitle_flag = "--write-subs" if kind == "manual" else "--write-auto-subs"
            download_result = run_ytdlp(
                [
                    "--skip-download",
                    subtitle_flag,
                    "--sub-langs",
                    language,
                    "--sub-format",
                    "json3",
                    "--sleep-subtitles",
                    str(delay),
                    "--no-warnings",
                    "-o",
                    str(temporary_dir / "caption.%(ext)s"),
                    metadata["url"],
                ]
            )
            files = sorted(temporary_dir.glob("*.json3"))
            valid, event_count = valid_json3(files[0]) if files else (False, 0)
            succeeded = download_result.returncode == 0 and valid
            item.update(
                {
                    "selected_kind": kind,
                    "selected_language": language,
                    "download_status": "success" if succeeded else "failed",
                    "event_count": event_count,
                }
            )
            if succeeded:
                temporary_destination = destination.with_suffix(".json3.tmp")
                shutil.copyfile(files[0], temporary_destination)
                temporary_destination.replace(destination)
                item["track_path"] = str(destination)
            else:
                item["download_error"] = error_text(download_result)
        report["videos"].append(item)
        time.sleep(delay)

    videos = report["videos"]
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["summary"] = {
        "total": len(videos),
        "info_success": sum(item.get("info_status") == "ok" for item in videos),
        "manual_english_advertised": sum(
            bool(item.get("manual_english_tracks")) for item in videos
        ),
        "automatic_english_advertised": sum(
            bool(item.get("automatic_english_tracks")) for item in videos
        ),
        "download_success": sum(
            item.get("download_status") in {"success", "cached"} for item in videos
        ),
        "download_failed": sum(item.get("download_status") == "failed" for item in videos),
        "no_track": sum(item.get("download_status") == "no_track" for item in videos),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_report = output_dir / "coverage.json.tmp"
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_report.replace(output_dir / "coverage.json")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure advertised and downloadable English tracks for cached YouTube videos."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments/english-track-download-coverage"),
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between videos")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must not be negative")
    print(
        json.dumps(
            probe(data_dir=args.data_dir, output_dir=args.output_dir, delay=args.delay),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
