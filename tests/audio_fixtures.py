"""Generated fixtures for the optional audio cache. No network and no real media."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

from speech_retrieval.audio import AUDIO_MANIFEST_SCHEMA_VERSION, raw_audio_paths
from speech_retrieval.identity import CACHE_SCHEMA_VERSION, track_id, video_key


def write_wave(
    path: Path,
    *,
    seconds: float = 1.0,
    sample_rate: int = 16_000,
    channels: int = 1,
    frequency: float = 220.0,
) -> Path:
    """Write a small deterministic PCM sine so ffmpeg has real audio to convert."""
    frames = int(seconds * sample_rate)
    samples = bytearray()
    for index in range(frames):
        value = int(12_000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        samples += struct.pack("<h", value) * channels
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(samples))
    return path


def stable_video_key(provider_video_id: str, language: str = "es") -> str:
    return video_key("youtube", language, provider_video_id)


def install_caption_video(
    data_dir: Path,
    *,
    provider_video_id: str = "video-1",
    language: str = "es",
    channel: str = "channel-a",
    caption_kind: str = "manual",
    duration: float = 120.0,
) -> str:
    """Create the minimal valid caption cache the audio cache is co-located with."""
    key = stable_video_key(provider_video_id, language)
    track = track_id(key, caption_kind, language)
    video_dir = data_dir / "raw" / "corpora" / language / key
    track_dir = video_dir / track
    track_dir.mkdir(parents=True, exist_ok=True)
    captions = json.dumps(
        {"events": [{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "hola"}]}]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    (track_dir / "subtitles.raw.json3").write_bytes(captions)
    (track_dir / "metadata.json").write_text(
        json.dumps(
            {
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "source_language": language,
                "video_key": key,
                "track_id": track,
                "video_id": provider_video_id,
                "provider": "youtube",
                "url": f"https://www.youtube.com/watch?v={provider_video_id}",
                "title": f"Fixture {provider_video_id}",
                "channel_config_id": channel,
                "channel": channel,
                "channel_id": channel,
                "duration": duration,
                "caption_kind": caption_kind,
                "caption_language": language,
                "content_sha256": hashlib.sha256(captions).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (video_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_schema_version": 1,
                "provider": "youtube",
                "video_key": key,
                "video_id": provider_video_id,
                "source_language": language,
                "canonical_source_track_id": track,
                "tracks": [
                    {
                        "track_id": track,
                        "kind": "authored" if caption_kind == "manual" else "automatic",
                        "is_source": True,
                        "status": "downloaded",
                    }
                ],
                "complete": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return key


def install_raw_audio(
    data_dir: Path,
    *,
    key: str,
    language: str = "es",
    provider_video_id: str = "video-1",
    source: Path | None = None,
    extension: str = "wav",
    duration: float = 2.0,
    format_name: str = "wav",
    codec_name: str = "pcm_s16le",
    sample_rate: int = 16_000,
    channels: int = 1,
    status: str = "ready",
    overrides: dict[str, Any] | None = None,
) -> Path:
    """Publish a ready or failed raw-audio manifest beside a caption cache."""
    paths = raw_audio_paths(data_dir, language=language, video_key=key)
    paths.directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "audio_manifest_schema_version": AUDIO_MANIFEST_SCHEMA_VERSION,
        "artifact": "raw_audio",
        "provider": "youtube",
        "source_language": language,
        "video_key": key,
        "video_id": provider_video_id,
        "status": status,
        "format_selection": {"format_id": "251", "reason": "smallest_known_size"},
        "attempts": [],
    }
    target = paths.source(extension)
    if status == "ready":
        if source is None:
            source = write_wave(paths.directory / "generated.wav", seconds=duration)
        if source != target:
            target.write_bytes(source.read_bytes())
            if source.parent == paths.directory:
                source.unlink()
        content = target.read_bytes()
        payload["raw_audio"] = {
            "filename": target.name,
            "extension": extension,
            "provider_format_id": "251",
            "size_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "duration": duration,
            "format_name": format_name,
            "codec_name": codec_name,
            "sample_rate": sample_rate,
            "channels": channels,
            "bit_rate": 256_000,
        }
        payload["acquired_at"] = "2026-09-07T00:00:00+00:00"
    else:
        payload["raw_audio"] = None
        payload["attempts"] = [
            {
                "attempted_at": "2026-09-07T00:00:00+00:00",
                "stage": "download",
                "error_code": "provider_temporary",
                "message": "HTTP Error 429: Too Many Requests",
                "retryable": True,
                "exhausted": True,
            }
        ]
        payload["failed_at"] = "2026-09-07T00:00:00+00:00"
    if overrides:
        payload.update(overrides)
    paths.manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def probe_payload(
    *,
    format_name: str = "wav",
    codec_name: str = "pcm_s16le",
    duration: float = 1.0,
    sample_rate: int = 16_000,
    channels: int = 1,
    include_video: bool = False,
) -> str:
    streams: list[dict[str, Any]] = [
        {
            "codec_type": "audio",
            "codec_name": codec_name,
            "sample_rate": str(sample_rate),
            "channels": channels,
            "duration": str(duration),
            "bit_rate": "256000",
        }
    ]
    if include_video:
        streams.append({"codec_type": "video", "codec_name": "vp9"})
    return json.dumps(
        {"streams": streams, "format": {"format_name": format_name, "duration": str(duration)}}
    )


def fake_conversion(
    *, duration_override: float | None = None, fail: bool = False, produce: bool = True
) -> Any:
    """Return an ffmpeg stand-in that generates a real WAV of the requested length."""
    from speech_retrieval.audio import AudioCacheError

    calls: list[list[str]] = []

    def run(arguments: Any) -> str:
        arguments = list(arguments)
        calls.append(arguments)
        if fail:
            raise AudioCacheError("ffmpeg Invalid data found when processing input")
        if not produce:
            return ""
        seconds = duration_override
        if seconds is None:
            seconds = float(arguments[arguments.index("-t") + 1])
        write_wave(Path(arguments[-1]), seconds=seconds)
        return ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def fake_probe_runner(duration: float) -> Any:
    def run(_arguments: Any) -> str:
        return probe_payload(duration=duration)

    return run
