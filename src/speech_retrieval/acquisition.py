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
    display_name: str | None = None
    is_translatable: bool = False


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
        entry = (info.get("subtitles") or {}).get(manual_keys[0]) or [{}]
        metadata = entry[0] if isinstance(entry, list) and entry else {}
        return CaptionTrack(
            "manual",
            manual_keys[0],
            metadata.get("name") if isinstance(metadata, dict) else None,
            bool(metadata.get("is_translatable")) if isinstance(metadata, dict) else False,
        )
    automatic_keys = _matching_track_keys(info.get("automatic_captions") or {}, source_language)
    if automatic_keys:
        entry = (info.get("automatic_captions") or {}).get(automatic_keys[0]) or [{}]
        metadata = entry[0] if isinstance(entry, list) and entry else {}
        return CaptionTrack(
            "automatic",
            automatic_keys[0],
            metadata.get("name") if isinstance(metadata, dict) else None,
            bool(metadata.get("is_translatable")) if isinstance(metadata, dict) else False,
        )
    return None


def authored_tracks(info: dict[str, Any]) -> list[CaptionTrack]:
    result: list[CaptionTrack] = []
    for language, entries in sorted((info.get("subtitles") or {}).items()):
        normalized = _provider_language(language)
        if normalized is None:
            continue
        metadata = entries[0] if isinstance(entries, list) and entries else {}
        result.append(
            CaptionTrack(
                "manual",
                language,
                metadata.get("name") if isinstance(metadata, dict) else None,
                bool(metadata.get("is_translatable")) if isinstance(metadata, dict) else False,
            )
        )
    return result


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


def _cached_manifest(
    video_dir: Path, *, source_language: str, expected_video_key: str
) -> dict[str, Any] | None:
    try:
        manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("manifest_schema_version") != 1
            or manifest.get("source_language") != source_language
            or manifest.get("video_key") != expected_video_key
            or not manifest.get("canonical_source_track_id")
            or not manifest.get("complete")
        ):
            return None
        tracks = manifest.get("tracks", [])
        source_tracks = [track for track in tracks if track.get("is_source")]
        if (
            len(source_tracks) != 1
            or source_tracks[0].get("track_id") != manifest["canonical_source_track_id"]
        ):
            return None
        for track in tracks:
            if track.get("status") not in {"downloaded", "cached"}:
                return None
            if not _valid_cache(
                video_dir / str(track["track_id"]),
                source_language=source_language,
                expected_video_key=expected_video_key,
            ):
                return None
        source_kind = source_tracks[0].get("kind")
        changed = False
        if "source_selection" not in manifest:
            manifest["source_selection"] = (
                "authored_source" if source_kind == "authored" else "automatic_source"
            )
            changed = True
        if "provenance" not in manifest:
            manifest["provenance"] = {
                "provider": "youtube",
                "acquisition": "yt-dlp",
            }
            changed = True
        if changed:
            _write_json(video_dir / "manifest.json", manifest)
        cached = dict(manifest)
        cached["tracks"] = [{**track, "status": "cached"} for track in manifest.get("tracks", [])]
        return cached
    except (OSError, ValueError, KeyError, TypeError):
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


def _download_track(
    *,
    track: CaptionTrack,
    is_source: bool,
    info: dict[str, Any],
    candidate: dict[str, Any],
    channel: Channel,
    catalogue: Catalogue,
    video_dir: Path,
    stable_video_key: str,
    raw_root: Path,
    runner: Runner,
) -> tuple[str, dict[str, Any]]:
    stable_track_id = track_id(stable_video_key, track.kind, track.language)
    target = video_dir / stable_track_id
    if _valid_cache(
        target,
        source_language=catalogue.language,
        expected_video_key=stable_video_key,
    ):
        metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        return "cached", metadata
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
            "provider": "youtube",
            "video_key": stable_video_key,
            "video_id": candidate["id"],
            "track_id": stable_track_id,
            "provider_track_id": track.language,
            "display_name": track.display_name,
            "is_source": is_source,
            "is_translatable": track.is_translatable,
            "url": info.get("webpage_url") or candidate["url"],
            "title": info.get("title") or candidate["title"],
            "channel_id": info.get("channel_id"),
            "channel": info.get("channel") or channel.name,
            "channel_config_id": channel.id,
            "duration": info.get("duration"),
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
        return "downloaded", metadata
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


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
    manifest = _cached_manifest(
        video_dir, source_language=catalogue.language, expected_video_key=stable_video_key
    )
    if manifest is not None:
        source = next(track for track in manifest["tracks"] if track.get("is_source"))
        metadata = json.loads(
            (video_dir / source["track_id"] / "metadata.json").read_text(encoding="utf-8")
        )
        return {
            "video_id": provider_video_id,
            "status": "cached",
            "metadata": metadata,
            "manifest": manifest,
        }
    legacy_cached = _cached_transcript(
        video_dir,
        source_language=catalogue.language,
        expected_video_key=stable_video_key,
    )
    try:
        info = _json_output(["--skip-download", candidate["url"]], runner)
    except Exception as error:
        if legacy_cached is None:
            raise
        legacy_manifest = {
            "manifest_schema_version": 1,
            "provider": provider,
            "video_key": stable_video_key,
            "video_id": provider_video_id,
            "source_language": catalogue.language,
            "canonical_source_track_id": legacy_cached["track_id"],
            "source_selection": (
                "authored_source"
                if legacy_cached["caption_kind"] == "manual"
                else "automatic_source"
            ),
            "provenance": {"provider": "youtube", "acquisition": "yt-dlp"},
            "tracks": [
                {
                    "track_id": legacy_cached["track_id"],
                    "provider_track_id": legacy_cached["caption_language"],
                    "language": _provider_language(legacy_cached["caption_language"])
                    or legacy_cached["caption_language"],
                    "display_name": None,
                    "kind": "authored"
                    if legacy_cached["caption_kind"] == "manual"
                    else "automatic",
                    "is_source": True,
                    "is_translatable": False,
                    "status": "cached",
                    "content_sha256": legacy_cached["content_sha256"],
                }
            ],
            "complete": False,
            "enumeration_error": str(error),
            "enumerated_at": datetime.now(UTC).isoformat(),
        }
        _write_json(video_dir / "manifest.json", legacy_manifest)
        return {
            "video_id": provider_video_id,
            "status": "cached",
            "metadata": legacy_cached,
            "manifest": legacy_manifest,
            "secondary_failures": [{"track_id": "enumeration", "error": str(error)}],
        }
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise AcquisitionError("video is live or upcoming")
    duration = info.get("duration")
    if duration is not None and float(duration) < 60:
        raise AcquisitionError("video is shorter than 60 seconds")
    track = select_caption_track(info, catalogue.language)
    if not track:
        raise AcquisitionError(f"no captions matching source language {catalogue.language}")

    source_status, metadata = _download_track(
        track=track,
        is_source=True,
        info=info,
        candidate=candidate,
        channel=channel,
        catalogue=catalogue,
        video_dir=video_dir,
        stable_video_key=stable_video_key,
        raw_root=raw_root,
        runner=runner,
    )
    source_track_id = metadata["track_id"]
    records: list[dict[str, Any]] = [
        {
            "track_id": source_track_id,
            "provider_track_id": track.language,
            "language": _provider_language(track.language) or track.language,
            "display_name": track.display_name,
            "kind": "authored" if track.kind == "manual" else "automatic",
            "is_source": True,
            "is_translatable": track.is_translatable,
            "status": source_status,
            "content_sha256": metadata["content_sha256"],
        }
    ]
    failures: list[dict[str, str]] = []
    for secondary in authored_tracks(info):
        secondary_id = track_id(stable_video_key, secondary.kind, secondary.language)
        if secondary_id == source_track_id:
            continue
        try:
            secondary_status, secondary_metadata = _download_track(
                track=secondary,
                is_source=False,
                info=info,
                candidate=candidate,
                channel=channel,
                catalogue=catalogue,
                video_dir=video_dir,
                stable_video_key=stable_video_key,
                raw_root=raw_root,
                runner=runner,
            )
            records.append(
                {
                    "track_id": secondary_id,
                    "provider_track_id": secondary.language,
                    "language": _provider_language(secondary.language) or secondary.language,
                    "display_name": secondary.display_name,
                    "kind": "authored",
                    "is_source": False,
                    "is_translatable": secondary.is_translatable,
                    "status": secondary_status,
                    "content_sha256": secondary_metadata["content_sha256"],
                }
            )
        except Exception as error:
            records.append(
                {
                    "track_id": secondary_id,
                    "provider_track_id": secondary.language,
                    "language": _provider_language(secondary.language) or secondary.language,
                    "display_name": secondary.display_name,
                    "kind": "authored",
                    "is_source": False,
                    "is_translatable": secondary.is_translatable,
                    "status": "failed",
                    "error": str(error),
                }
            )
            failures.append({"track_id": secondary_id, "error": str(error)})
    manifest = {
        "manifest_schema_version": 1,
        "provider": provider,
        "video_key": stable_video_key,
        "video_id": provider_video_id,
        "source_language": catalogue.language,
        "canonical_source_track_id": source_track_id,
        "source_selection": "authored_source" if track.kind == "manual" else "automatic_source",
        "provenance": {"provider": "youtube", "acquisition": "yt-dlp"},
        "tracks": records,
        "complete": not failures,
        "enumerated_at": datetime.now(UTC).isoformat(),
    }
    _write_json(video_dir / "manifest.json", manifest)
    return {
        "video_id": provider_video_id,
        "status": source_status,
        "metadata": metadata,
        "manifest": manifest,
        "secondary_failures": failures,
    }


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
        "authored_secondary_downloaded": 0,
        "authored_secondary_cached": 0,
        "authored_secondary_failed": 0,
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
                manifest = result.get("manifest") or {}
                secondary_tracks = [
                    track for track in manifest.get("tracks", []) if not track.get("is_source")
                ]
                report["authored_secondary_downloaded"] += sum(
                    track.get("status") == "downloaded" for track in secondary_tracks
                )
                report["authored_secondary_cached"] += sum(
                    track.get("status") == "cached" for track in secondary_tracks
                )
                report["authored_secondary_failed"] += sum(
                    track.get("status") == "failed" for track in secondary_tracks
                )
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
                        "source_selection": (
                            "authored_source"
                            if metadata["caption_kind"] == "manual"
                            else "automatic_source"
                        ),
                        "authored_secondary_tracks": len(secondary_tracks),
                        "authored_secondary_failures": result.get("secondary_failures", []),
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
