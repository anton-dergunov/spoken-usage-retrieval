from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .catalogue import canonical_language
from .contracts import AudioCacheStatus, AudioChannelStatus, AudioLanguageStatus
from .identity import clip_id

AUDIO_PREPARATION_VERSION = "pcm-s16le-mono-16000-v1"
AUDIO_MANIFEST_SCHEMA_VERSION = 1
CLIP_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_CLIP_TOLERANCE_MS = 50
SHA256_RE = re.compile(r"[0-9a-f]{64}")
VIDEO_KEY_RE = re.compile(r"vid_[0-9a-f]{20}")
CLIP_KEY_RE = re.compile(r"clp_[0-9a-f]{20}")
MEDIA_EXTENSION_RE = re.compile(r"[a-z0-9]{1,10}")

AudioStatus = Literal["ready", "failed", "missing"]


class AudioProbeError(RuntimeError):
    pass


class AudioCacheError(RuntimeError):
    """Raised when the audio cache cannot satisfy a clip or storage request."""


ProbeRunner = Callable[[Sequence[str]], str]


@dataclass(frozen=True, slots=True)
class AudioProbe:
    path: Path
    duration: float
    size_bytes: int
    content_sha256: str
    format_name: str
    codec_name: str
    sample_rate: int
    channels: int
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class PreparedClipPaths:
    directory: Path
    clip: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class RawAudioPaths:
    directory: Path
    manifest: Path

    def source(self, extension: str) -> Path:
        if not isinstance(extension, str) or MEDIA_EXTENSION_RE.fullmatch(extension) is None:
            raise ValueError("audio extension must contain only lowercase letters and digits")
        return self.directory / f"source.{extension}"


def raw_audio_paths(data_dir: Path, *, language: str, video_key: str) -> RawAudioPaths:
    language = canonical_language(language)
    if VIDEO_KEY_RE.fullmatch(video_key) is None:
        raise ValueError("video_key must be a stable video ID")
    directory = Path(data_dir) / "raw" / "corpora" / language / video_key / "audio"
    return RawAudioPaths(directory=directory, manifest=directory / "manifest.json")


def prepared_clip_paths(
    data_dir: Path,
    *,
    language: str,
    video_key: str,
    clip_key: str,
) -> PreparedClipPaths:
    language = canonical_language(language)
    if VIDEO_KEY_RE.fullmatch(video_key) is None:
        raise ValueError("video_key must be a stable video ID")
    if CLIP_KEY_RE.fullmatch(clip_key) is None:
        raise ValueError("clip_key must be a stable clip ID")
    directory = Path(data_dir) / "derived" / "audio" / "clips" / language / video_key / clip_key
    return PreparedClipPaths(
        directory=directory,
        clip=directory / "clip.wav",
        manifest=directory / "manifest.json",
    )


def ffprobe_runner(executable: str = "ffprobe", *, timeout: float = 30.0) -> ProbeRunner:
    """Return a probe runner that shells out to a specific ffprobe executable."""

    def run(arguments: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                [executable, *arguments],
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except OSError as error:
            raise AudioProbeError(f"{executable} could not be executed: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise AudioProbeError(f"{executable} timed out") from error
        if result.returncode:
            message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed"
            raise AudioProbeError(f"ffprobe {message}")
        return result.stdout

    return run


_run_ffprobe = ffprobe_runner()


def tool_version(executable: str, *, timeout: float = 15.0) -> str | None:
    """Return the first line of ``<executable> -version``, or None when unavailable."""
    try:
        result = subprocess.run(
            [executable, "-version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def _positive_number(*values: Any) -> float:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    raise AudioProbeError("audio duration is missing or invalid")


def _positive_integer(value: Any, *, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise AudioProbeError(f"audio {name} is missing or invalid") from error
    if isinstance(value, bool) or number <= 0:
        raise AudioProbeError(f"audio {name} is missing or invalid")
    return number


def _optional_positive_integer(*values: Any) -> int | None:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not isinstance(value, bool) and number > 0:
            return number
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_audio(path: Path, *, runner: ProbeRunner = _run_ffprobe) -> AudioProbe:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AudioProbeError(f"audio file does not exist or is not a regular file: {path}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise AudioProbeError("audio file is empty")
    arguments = [
        "-v",
        "error",
        "-show_entries",
        (
            "format=format_name,duration,bit_rate:"
            "stream=codec_type,codec_name,sample_rate,channels,duration,bit_rate"
        ),
        "-of",
        "json",
        str(path),
    ]
    try:
        payload = json.loads(runner(arguments))
    except json.JSONDecodeError as error:
        raise AudioProbeError("ffprobe returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise AudioProbeError("ffprobe returned an invalid payload")
    streams = payload.get("streams")
    if not isinstance(streams, list) or not all(isinstance(item, dict) for item in streams):
        raise AudioProbeError("ffprobe returned invalid stream metadata")
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    if len(audio_streams) != 1:
        raise AudioProbeError(f"expected one audio stream, found {len(audio_streams)}")
    if video_streams:
        raise AudioProbeError("audio source contains a video stream")
    stream = audio_streams[0]
    format_metadata = payload.get("format")
    if not isinstance(format_metadata, dict):
        raise AudioProbeError("ffprobe returned invalid format metadata")
    format_name = format_metadata.get("format_name")
    codec_name = stream.get("codec_name")
    if not isinstance(format_name, str) or not format_name:
        raise AudioProbeError("audio format name is missing")
    if not isinstance(codec_name, str) or not codec_name:
        raise AudioProbeError("audio codec name is missing")
    return AudioProbe(
        path=path,
        duration=_positive_number(format_metadata.get("duration"), stream.get("duration")),
        size_bytes=size_bytes,
        content_sha256=_sha256(path),
        format_name=format_name,
        codec_name=codec_name,
        sample_rate=_positive_integer(stream.get("sample_rate"), name="sample rate"),
        channels=_positive_integer(stream.get("channels"), name="channel count"),
        bit_rate=_optional_positive_integer(
            stream.get("bit_rate"), format_metadata.get("bit_rate")
        ),
    )


def validate_audio_integrity(
    probe: AudioProbe,
    *,
    expected_size_bytes: int,
    expected_sha256: str,
) -> None:
    if (
        isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or expected_size_bytes <= 0
    ):
        raise ValueError("expected_size_bytes must be positive")
    if not isinstance(expected_sha256, str) or SHA256_RE.fullmatch(expected_sha256) is None:
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
    if probe.size_bytes != expected_size_bytes:
        raise AudioProbeError("audio size does not match its manifest")
    if probe.content_sha256 != expected_sha256:
        raise AudioProbeError("audio checksum does not match its manifest")


def _milliseconds(value: float, *, name: str, exact: bool = True) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    milliseconds = round(number * 1000)
    if exact and not math.isclose(number, milliseconds / 1000, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"{name} must use millisecond precision")
    return milliseconds


@dataclass(frozen=True, slots=True)
class AudioClipRange:
    requested_start_ms: int
    requested_end_ms: int
    padding_ms: int
    effective_start_ms: int
    effective_end_ms: int

    @classmethod
    def from_seconds(
        cls,
        start: float,
        end: float,
        *,
        source_duration: float,
        padding: float = 0,
    ) -> AudioClipRange:
        requested_start_ms = _milliseconds(start, name="start")
        requested_end_ms = _milliseconds(end, name="end")
        padding_ms = _milliseconds(padding, name="padding")
        source_duration_ms = _milliseconds(source_duration, name="source_duration", exact=False)
        if requested_start_ms < 0:
            raise ValueError("start must not be negative")
        if requested_end_ms <= requested_start_ms:
            raise ValueError("end must be greater than start")
        if padding_ms < 0:
            raise ValueError("padding must not be negative")
        if source_duration_ms <= 0:
            raise ValueError("source_duration must be positive")
        effective_start_ms = max(0, requested_start_ms - padding_ms)
        effective_end_ms = min(source_duration_ms, requested_end_ms + padding_ms)
        if effective_start_ms >= effective_end_ms:
            raise ValueError("requested range does not overlap the source audio")
        return cls(
            requested_start_ms=requested_start_ms,
            requested_end_ms=requested_end_ms,
            padding_ms=padding_ms,
            effective_start_ms=effective_start_ms,
            effective_end_ms=effective_end_ms,
        )

    def cache_key(
        self,
        source_sha256: str,
        *,
        preparation_version: str = AUDIO_PREPARATION_VERSION,
    ) -> str:
        if SHA256_RE.fullmatch(source_sha256) is None:
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if not preparation_version.strip():
            raise ValueError("preparation_version must not be empty")
        return clip_id(
            source_sha256=source_sha256,
            requested_start_ms=self.requested_start_ms,
            requested_end_ms=self.requested_end_ms,
            padding_ms=self.padding_ms,
            effective_start_ms=self.effective_start_ms,
            effective_end_ms=self.effective_end_ms,
            preparation_version=preparation_version,
        )


def validate_prepared_clip(
    probe: AudioProbe,
    clip_range: AudioClipRange,
    *,
    duration_tolerance_ms: int = 50,
) -> None:
    if (
        isinstance(duration_tolerance_ms, bool)
        or not isinstance(duration_tolerance_ms, int)
        or duration_tolerance_ms < 0
    ):
        raise ValueError("duration_tolerance_ms must be a nonnegative integer")
    if probe.format_name != "wav":
        raise AudioProbeError("prepared clip must use the WAV container")
    if probe.codec_name != "pcm_s16le":
        raise AudioProbeError("prepared clip must use signed 16-bit PCM")
    if probe.sample_rate != 16000:
        raise AudioProbeError("prepared clip must use a 16 kHz sample rate")
    if probe.channels != 1:
        raise AudioProbeError("prepared clip must be mono")
    expected_ms = clip_range.effective_end_ms - clip_range.effective_start_ms
    actual_ms = round(probe.duration * 1000)
    if abs(actual_ms - expected_ms) > duration_tolerance_ms:
        raise AudioProbeError(
            f"prepared clip duration differs from the requested range: "
            f"expected {expected_ms} ms, got {actual_ms} ms"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass(frozen=True, slots=True)
class RawAudioRecord:
    """Readiness and provenance of one video's immutable source audio."""

    language: str
    video_key: str
    status: AudioStatus
    source_path: Path | None = None
    extension: str | None = None
    duration: float | None = None
    size_bytes: int | None = None
    content_sha256: str | None = None
    format_name: str | None = None
    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    provider_format_id: str | None = None
    acquired_at: str | None = None
    error: str | None = None
    manifest: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def read_raw_audio_manifest(paths: RawAudioPaths) -> dict[str, Any] | None:
    """Return a structurally valid raw-audio manifest, or None."""
    payload = _read_json(paths.manifest)
    if not isinstance(payload, dict):
        return None
    if payload.get("audio_manifest_schema_version") != AUDIO_MANIFEST_SCHEMA_VERSION:
        return None
    if payload.get("status") not in {"ready", "failed"}:
        return None
    return payload


def _last_error(manifest: dict[str, Any]) -> str | None:
    attempts = manifest.get("attempts")
    if isinstance(attempts, list) and attempts:
        last = attempts[-1]
        if isinstance(last, dict):
            stage = last.get("stage") or "acquisition"
            message = last.get("message") or "audio acquisition failed"
            return f"{stage}: {message}"
    error = manifest.get("error")
    return str(error) if error else None


def audio_availability(
    data_dir: Path,
    *,
    language: str,
    video_key: str,
    verify_checksum: bool = False,
) -> RawAudioRecord:
    """Report whether one video's source audio is ready, failed, or missing.

    Existence and byte count of the declared payload are always checked. The full content
    checksum is only recomputed when ``verify_checksum`` is requested, because acquisition
    needs that guarantee while status reporting does not.
    """
    language = canonical_language(language)
    paths = raw_audio_paths(data_dir, language=language, video_key=video_key)
    manifest = read_raw_audio_manifest(paths)
    if manifest is None:
        return RawAudioRecord(language=language, video_key=video_key, status="missing")
    if manifest.get("status") == "failed":
        return RawAudioRecord(
            language=language,
            video_key=video_key,
            status="failed",
            error=_last_error(manifest),
            manifest=manifest,
        )
    raw = manifest.get("raw_audio")
    if not isinstance(raw, dict):
        return RawAudioRecord(
            language=language,
            video_key=video_key,
            status="failed",
            error="ready audio manifest has no raw_audio record",
            manifest=manifest,
        )
    extension = raw.get("extension")
    checksum = raw.get("content_sha256")
    size_bytes = raw.get("size_bytes")
    try:
        source = paths.source(str(extension))
    except ValueError:
        return RawAudioRecord(
            language=language,
            video_key=video_key,
            status="failed",
            error="ready audio manifest declares an unusable payload extension",
            manifest=manifest,
        )
    problem: str | None = None
    if not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None:
        problem = "ready audio manifest has no valid checksum"
    elif not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        problem = "ready audio manifest has no valid byte count"
    elif source.is_symlink() or not source.is_file():
        problem = "cached audio payload is missing"
    elif source.stat().st_size != size_bytes:
        problem = "cached audio size does not match its manifest"
    elif verify_checksum and _sha256(source) != checksum:
        problem = "cached audio checksum does not match its manifest"
    if problem is not None:
        return RawAudioRecord(
            language=language,
            video_key=video_key,
            status="failed",
            error=problem,
            manifest=manifest,
        )
    return RawAudioRecord(
        language=language,
        video_key=video_key,
        status="ready",
        source_path=source,
        extension=str(extension),
        duration=float(raw["duration"]) if raw.get("duration") is not None else None,
        size_bytes=int(size_bytes) if isinstance(size_bytes, int) else None,
        content_sha256=str(checksum),
        format_name=raw.get("format_name"),
        codec_name=raw.get("codec_name"),
        sample_rate=raw.get("sample_rate"),
        channels=raw.get("channels"),
        provider_format_id=raw.get("provider_format_id"),
        acquired_at=manifest.get("acquired_at"),
        manifest=manifest,
    )


ConversionRunner = Callable[[Sequence[str]], str]


def ffmpeg_runner(executable: str = "ffmpeg", *, timeout: float = 300.0) -> ConversionRunner:
    """Return a conversion runner that shells out to a specific ffmpeg executable."""

    def run(arguments: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                [executable, *arguments],
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            raise AudioCacheError(f"{executable} could not be executed: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise AudioCacheError(f"{executable} timed out") from error
        if result.returncode:
            message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed"
            raise AudioCacheError(f"ffmpeg {message}")
        return result.stdout

    return run


@dataclass(frozen=True, slots=True)
class PreparedClip:
    """A cached 16 kHz mono PCM clip with the provenance later stages need."""

    path: Path
    manifest_path: Path
    clip_key: str
    language: str
    video_key: str
    source_sha256: str
    clip_range: AudioClipRange
    requested_start: float
    requested_end: float
    effective_start: float
    effective_end: float
    padding_before: float
    padding_after: float
    duration: float
    sample_rate: int
    channels: int
    sample_format: str
    size_bytes: int
    content_sha256: str
    preparation_version: str
    ffmpeg_version: str | None
    created_at: str
    cache_hit: bool


def _clip_manifest_payload(
    *,
    clip_key: str,
    language: str,
    video_key: str,
    source_sha256: str,
    clip_range: AudioClipRange,
    probe: AudioProbe,
    preparation_version: str,
    ffmpeg_version: str | None,
    created_at: str,
) -> dict[str, Any]:
    return {
        "clip_manifest_schema_version": CLIP_MANIFEST_SCHEMA_VERSION,
        "artifact": "derived_clip",
        "clip_key": clip_key,
        "source_language": language,
        "video_key": video_key,
        "source_sha256": source_sha256,
        "preparation_version": preparation_version,
        "requested_start_ms": clip_range.requested_start_ms,
        "requested_end_ms": clip_range.requested_end_ms,
        "padding_ms": clip_range.padding_ms,
        "effective_start_ms": clip_range.effective_start_ms,
        "effective_end_ms": clip_range.effective_end_ms,
        "padding_before_ms": clip_range.requested_start_ms - clip_range.effective_start_ms,
        "padding_after_ms": clip_range.effective_end_ms - clip_range.requested_end_ms,
        "requested_start": clip_range.requested_start_ms / 1000,
        "requested_end": clip_range.requested_end_ms / 1000,
        "effective_start": clip_range.effective_start_ms / 1000,
        "effective_end": clip_range.effective_end_ms / 1000,
        "duration": probe.duration,
        "sample_rate": probe.sample_rate,
        "channels": probe.channels,
        "sample_format": "s16le",
        "size_bytes": probe.size_bytes,
        "content_sha256": probe.content_sha256,
        "ffmpeg_version": ffmpeg_version,
        "created_at": created_at,
    }


def read_clip_manifest(paths: PreparedClipPaths) -> dict[str, Any] | None:
    payload = _read_json(paths.manifest)
    if not isinstance(payload, dict):
        return None
    if payload.get("clip_manifest_schema_version") != CLIP_MANIFEST_SCHEMA_VERSION:
        return None
    return payload


def _cached_clip(
    paths: PreparedClipPaths,
    *,
    clip_key: str,
    language: str,
    video_key: str,
    source_sha256: str,
    clip_range: AudioClipRange,
    preparation_version: str,
) -> PreparedClip | None:
    manifest = read_clip_manifest(paths)
    if manifest is None:
        return None
    expected = {
        "clip_key": clip_key,
        "source_language": language,
        "video_key": video_key,
        "source_sha256": source_sha256,
        "preparation_version": preparation_version,
        "requested_start_ms": clip_range.requested_start_ms,
        "requested_end_ms": clip_range.requested_end_ms,
        "padding_ms": clip_range.padding_ms,
        "effective_start_ms": clip_range.effective_start_ms,
        "effective_end_ms": clip_range.effective_end_ms,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        return None
    checksum = manifest.get("content_sha256")
    size_bytes = manifest.get("size_bytes")
    if not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None:
        return None
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        return None
    clip = paths.clip
    if clip.is_symlink() or not clip.is_file():
        return None
    if clip.stat().st_size != size_bytes or _sha256(clip) != checksum:
        return None
    return _prepared_clip_from_manifest(paths, manifest, clip_range, cache_hit=True)


def _prepared_clip_from_manifest(
    paths: PreparedClipPaths,
    manifest: dict[str, Any],
    clip_range: AudioClipRange,
    *,
    cache_hit: bool,
) -> PreparedClip:
    return PreparedClip(
        path=paths.clip,
        manifest_path=paths.manifest,
        clip_key=str(manifest["clip_key"]),
        language=str(manifest["source_language"]),
        video_key=str(manifest["video_key"]),
        source_sha256=str(manifest["source_sha256"]),
        clip_range=clip_range,
        requested_start=float(manifest["requested_start"]),
        requested_end=float(manifest["requested_end"]),
        effective_start=float(manifest["effective_start"]),
        effective_end=float(manifest["effective_end"]),
        padding_before=float(manifest["padding_before_ms"]) / 1000,
        padding_after=float(manifest["padding_after_ms"]) / 1000,
        duration=float(manifest["duration"]),
        sample_rate=int(manifest["sample_rate"]),
        channels=int(manifest["channels"]),
        sample_format=str(manifest["sample_format"]),
        size_bytes=int(manifest["size_bytes"]),
        content_sha256=str(manifest["content_sha256"]),
        preparation_version=str(manifest["preparation_version"]),
        ffmpeg_version=manifest.get("ffmpeg_version"),
        created_at=str(manifest["created_at"]),
        cache_hit=cache_hit,
    )


def conversion_arguments(source: Path, target: Path, clip_range: AudioClipRange) -> list[str]:
    """Return the fixed ffmpeg arguments that realize the named preparation contract."""
    start = clip_range.effective_start_ms / 1000
    duration = (clip_range.effective_end_ms - clip_range.effective_start_ms) / 1000
    return [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-accurate_seek",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(target),
    ]


def prepare_clip(
    data_dir: Path,
    *,
    language: str,
    video_key: str,
    start: float,
    end: float,
    padding: float = 0.0,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    probe_runner: ProbeRunner | None = None,
    conversion_runner: ConversionRunner | None = None,
    duration_tolerance_ms: int = DEFAULT_CLIP_TOLERANCE_MS,
    preparation_version: str = AUDIO_PREPARATION_VERSION,
    ffmpeg_version: str | None = None,
) -> PreparedClip:
    """Return a cached 16 kHz mono WAV covering ``start``-``end`` of a video's source audio.

    The clip is identified by source checksum, requested range, padding, clamped effective
    range, and preparation version, so a changed conversion contract can never reuse an old
    file. Callers pass ``padding=0`` for ranges that are already padded.
    """
    language = canonical_language(language)
    record = audio_availability(data_dir, language=language, video_key=video_key)
    if not record.ready or record.source_path is None or record.content_sha256 is None:
        raise AudioCacheError(
            f"source audio is not available for {video_key}: {record.error or record.status}"
        )
    if record.duration is None:
        raise AudioCacheError(f"source audio duration is unknown for {video_key}")
    clip_range = AudioClipRange.from_seconds(
        start,
        end,
        source_duration=record.duration,
        padding=padding,
    )
    clip_key = clip_range.cache_key(record.content_sha256, preparation_version=preparation_version)
    paths = prepared_clip_paths(data_dir, language=language, video_key=video_key, clip_key=clip_key)
    cached = _cached_clip(
        paths,
        clip_key=clip_key,
        language=language,
        video_key=video_key,
        source_sha256=record.content_sha256,
        clip_range=clip_range,
        preparation_version=preparation_version,
    )
    if cached is not None:
        return cached
    probe = probe_runner if probe_runner is not None else ffprobe_runner(ffprobe)
    convert = conversion_runner if conversion_runner is not None else ffmpeg_runner(ffmpeg)
    paths.directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{clip_key}-", dir=paths.directory.parent))
    try:
        staged_clip = staging / paths.clip.name
        convert(conversion_arguments(record.source_path, staged_clip, clip_range))
        if not staged_clip.is_file() or staged_clip.stat().st_size <= 0:
            raise AudioCacheError("clip conversion produced no audio")
        clip_probe = probe_audio(staged_clip, runner=probe)
        validate_prepared_clip(clip_probe, clip_range, duration_tolerance_ms=duration_tolerance_ms)
        created_at = _now()
        manifest = _clip_manifest_payload(
            clip_key=clip_key,
            language=language,
            video_key=video_key,
            source_sha256=record.content_sha256,
            clip_range=clip_range,
            probe=clip_probe,
            preparation_version=preparation_version,
            ffmpeg_version=ffmpeg_version if ffmpeg_version is not None else tool_version(ffmpeg),
            created_at=created_at,
        )
        (staging / paths.manifest.name).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        concurrent = _cached_clip(
            paths,
            clip_key=clip_key,
            language=language,
            video_key=video_key,
            source_sha256=record.content_sha256,
            clip_range=clip_range,
            preparation_version=preparation_version,
        )
        if concurrent is not None:
            return concurrent
        if paths.directory.exists():
            shutil.rmtree(paths.directory, ignore_errors=True)
        staging.replace(paths.directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _prepared_clip_from_manifest(paths, manifest, clip_range, cache_hit=False)


@dataclass(frozen=True, slots=True)
class AudioStorageIssue:
    """An unrecognized or orphaned file that totals must not silently absorb."""

    path: Path
    kind: Literal["orphan", "unrecognized", "invalid_manifest"]
    reason: str
    bytes: int = 0


@dataclass(frozen=True, slots=True)
class AudioVideoStorage:
    language: str
    video_key: str
    channel: str | None
    status: AudioStatus
    duration: float | None
    raw_bytes: int
    raw_files: int
    derived_bytes: int
    derived_clips: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AudioChannelStorage:
    language: str
    channel: str | None
    videos: int
    ready: int
    failed: int
    missing: int
    raw_bytes: int
    derived_bytes: int
    derived_clips: int


@dataclass(frozen=True, slots=True)
class AudioLanguageStorage:
    language: str
    videos: int
    ready: int
    failed: int
    missing: int
    raw_bytes: int
    raw_files: int
    derived_bytes: int
    derived_clips: int
    channels: tuple[AudioChannelStorage, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioStorageSummary:
    """Logical byte totals and readiness counts reconciled against files on disk."""

    generated_at: str
    videos: int
    ready: int
    failed: int
    missing: int
    raw_bytes: int
    raw_files: int
    derived_bytes: int
    derived_clips: int
    languages: tuple[AudioLanguageStorage, ...] = ()
    video_details: tuple[AudioVideoStorage, ...] = ()
    issues: tuple[AudioStorageIssue, ...] = ()


def _caption_video_directories(data_dir: Path, languages: Sequence[str] | None) -> Iterator[Path]:
    root = Path(data_dir) / "raw" / "corpora"
    if not root.is_dir():
        return
    selected = {canonical_language(item) for item in languages} if languages else None
    for language_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            language = canonical_language(language_dir.name)
        except ValueError:
            continue
        if language != language_dir.name or (selected is not None and language not in selected):
            continue
        for video_dir in sorted(path for path in language_dir.iterdir() if path.is_dir()):
            if VIDEO_KEY_RE.fullmatch(video_dir.name) is None:
                continue
            yield video_dir


def _video_channel(video_dir: Path) -> str | None:
    manifest = _read_json(video_dir / "manifest.json")
    if not isinstance(manifest, dict):
        return None
    track = manifest.get("canonical_source_track_id")
    if not isinstance(track, str) or not track:
        return None
    metadata = _read_json(video_dir / track / "metadata.json")
    if not isinstance(metadata, dict):
        return None
    channel = metadata.get("channel_config_id")
    return str(channel) if channel else None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _raw_audio_usage(
    record: RawAudioRecord, paths: RawAudioPaths
) -> tuple[int, int, list[AudioStorageIssue]]:
    if not paths.directory.is_dir():
        return 0, 0, []
    recognized = {paths.manifest.name}
    total = _size(paths.manifest) if paths.manifest.is_file() else 0
    files = 1 if paths.manifest.is_file() else 0
    if record.status == "ready" and record.source_path is not None:
        recognized.add(record.source_path.name)
        total += _size(record.source_path)
        files += 1
    issues: list[AudioStorageIssue] = []
    for entry in sorted(paths.directory.iterdir()):
        if entry.name in recognized:
            continue
        issues.append(
            AudioStorageIssue(
                path=entry,
                kind="unrecognized",
                reason="file is not declared by the audio manifest",
                bytes=_size(entry) if entry.is_file() else 0,
            )
        )
    return total, files, issues


def _derived_clip_usage(
    data_dir: Path, *, language: str, video_key: str
) -> tuple[int, int, list[AudioStorageIssue]]:
    root = Path(data_dir) / "derived" / "audio" / "clips" / language / video_key
    if not root.is_dir():
        return 0, 0, []
    total = 0
    clips = 0
    issues: list[AudioStorageIssue] = []
    for clip_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if CLIP_KEY_RE.fullmatch(clip_dir.name) is None:
            issues.append(
                AudioStorageIssue(
                    path=clip_dir,
                    kind="unrecognized",
                    reason="directory is not a stable clip key",
                )
            )
            continue
        paths = prepared_clip_paths(
            data_dir, language=language, video_key=video_key, clip_key=clip_dir.name
        )
        manifest = read_clip_manifest(paths)
        if manifest is None:
            issues.append(
                AudioStorageIssue(
                    path=clip_dir,
                    kind="invalid_manifest",
                    reason="clip manifest is missing or unreadable",
                    bytes=sum(_size(item) for item in clip_dir.iterdir() if item.is_file()),
                )
            )
            continue
        clips += 1
        total += _size(paths.manifest) + _size(paths.clip)
        for entry in sorted(clip_dir.iterdir()):
            if entry.name in {paths.clip.name, paths.manifest.name}:
                continue
            issues.append(
                AudioStorageIssue(
                    path=entry,
                    kind="unrecognized",
                    reason="file is not declared by the clip manifest",
                    bytes=_size(entry) if entry.is_file() else 0,
                )
            )
    return total, clips, issues


def _orphan_clip_issues(data_dir: Path, known: set[tuple[str, str]]) -> list[AudioStorageIssue]:
    root = Path(data_dir) / "derived" / "audio" / "clips"
    if not root.is_dir():
        return []
    issues: list[AudioStorageIssue] = []
    for language_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for video_dir in sorted(path for path in language_dir.iterdir() if path.is_dir()):
            if (language_dir.name, video_dir.name) in known:
                continue
            issues.append(
                AudioStorageIssue(
                    path=video_dir,
                    kind="orphan",
                    reason="derived clips have no corresponding caption cache",
                    bytes=sum(_size(item) for item in video_dir.rglob("*") if item.is_file()),
                )
            )
    return issues


def audio_storage(data_dir: Path, *, languages: Sequence[str] | None = None) -> AudioStorageSummary:
    """Summarize raw and derived audio usage by language, channel, and video."""
    details: list[AudioVideoStorage] = []
    issues: list[AudioStorageIssue] = []
    known: set[tuple[str, str]] = set()
    for video_dir in _caption_video_directories(data_dir, languages):
        language = video_dir.parent.name
        video_key = video_dir.name
        known.add((language, video_key))
        record = audio_availability(data_dir, language=language, video_key=video_key)
        paths = raw_audio_paths(data_dir, language=language, video_key=video_key)
        raw_bytes, raw_files, raw_issues = _raw_audio_usage(record, paths)
        derived_bytes, derived_clips, clip_issues = _derived_clip_usage(
            data_dir, language=language, video_key=video_key
        )
        issues.extend(raw_issues)
        issues.extend(clip_issues)
        details.append(
            AudioVideoStorage(
                language=language,
                video_key=video_key,
                channel=_video_channel(video_dir),
                status=record.status,
                duration=record.duration,
                raw_bytes=raw_bytes,
                raw_files=raw_files,
                derived_bytes=derived_bytes,
                derived_clips=derived_clips,
                error=record.error,
            )
        )
    if languages is None:
        issues.extend(_orphan_clip_issues(data_dir, known))
    languages_summary: list[AudioLanguageStorage] = []
    for language in sorted({item.language for item in details}):
        rows = [item for item in details if item.language == language]
        channels: list[AudioChannelStorage] = []
        for channel in sorted({item.channel or "" for item in rows}):
            channel_rows = [item for item in rows if (item.channel or "") == channel]
            channels.append(
                AudioChannelStorage(
                    language=language,
                    channel=channel or None,
                    videos=len(channel_rows),
                    ready=sum(item.status == "ready" for item in channel_rows),
                    failed=sum(item.status == "failed" for item in channel_rows),
                    missing=sum(item.status == "missing" for item in channel_rows),
                    raw_bytes=sum(item.raw_bytes for item in channel_rows),
                    derived_bytes=sum(item.derived_bytes for item in channel_rows),
                    derived_clips=sum(item.derived_clips for item in channel_rows),
                )
            )
        languages_summary.append(
            AudioLanguageStorage(
                language=language,
                videos=len(rows),
                ready=sum(item.status == "ready" for item in rows),
                failed=sum(item.status == "failed" for item in rows),
                missing=sum(item.status == "missing" for item in rows),
                raw_bytes=sum(item.raw_bytes for item in rows),
                raw_files=sum(item.raw_files for item in rows),
                derived_bytes=sum(item.derived_bytes for item in rows),
                derived_clips=sum(item.derived_clips for item in rows),
                channels=tuple(channels),
            )
        )
    return AudioStorageSummary(
        generated_at=_now(),
        videos=len(details),
        ready=sum(item.status == "ready" for item in details),
        failed=sum(item.status == "failed" for item in details),
        missing=sum(item.status == "missing" for item in details),
        raw_bytes=sum(item.raw_bytes for item in details),
        raw_files=sum(item.raw_files for item in details),
        derived_bytes=sum(item.derived_bytes for item in details),
        derived_clips=sum(item.derived_clips for item in details),
        languages=tuple(languages_summary),
        video_details=tuple(details),
        issues=tuple(issues),
    )


@dataclass(frozen=True, slots=True)
class AudioPruneRequest:
    """Explicit prune selectors. Ordinary pruning never touches captions or raw media."""

    languages: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    video_keys: tuple[str, ...] = ()
    preparation_versions: tuple[str, ...] = ()
    older_than_days: int | None = None
    include_raw_audio: bool = False
    select_all: bool = False
    stale_temporary_hours: float = 24.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "languages",
            tuple(canonical_language(item) for item in self.languages),
        )
        if self.older_than_days is not None and self.older_than_days < 0:
            raise ValueError("older_than_days must not be negative")
        if self.stale_temporary_hours < 0:
            raise ValueError("stale_temporary_hours must not be negative")
        if not self.select_all and not (
            self.languages
            or self.channels
            or self.video_keys
            or self.preparation_versions
            or self.older_than_days is not None
        ):
            raise ValueError("choose at least one prune selector or pass select_all")

    def matches_video(self, *, language: str, video_key: str, channel: str | None) -> bool:
        if self.languages and language not in self.languages:
            return False
        if self.video_keys and video_key not in self.video_keys:
            return False
        if self.channels and (channel or "") not in self.channels:
            return False
        return True


@dataclass(frozen=True, slots=True)
class AudioPruneEntry:
    path: Path
    kind: Literal["derived_clip", "raw_audio", "stale_temporary"]
    language: str
    video_key: str
    clip_key: str | None
    bytes: int
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class AudioPrunePlan:
    """The same structured plan is returned in preview and execute modes."""

    generated_at: str
    dry_run: bool
    entries: tuple[AudioPruneEntry, ...] = ()
    orphaned_clips: tuple[Path, ...] = ()
    derived_clips: int = 0
    derived_bytes: int = 0
    raw_videos: int = 0
    raw_bytes: int = 0
    stale_temporary: int = 0
    stale_temporary_bytes: int = 0


def _directory_bytes(path: Path) -> int:
    return sum(_size(item) for item in path.rglob("*") if item.is_file())


def _older_than_hours(path: Path, hours: float) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds >= hours * 3_600


def _older_than(path: Path, older_than_days: int | None) -> bool:
    if older_than_days is None:
        return True
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds >= older_than_days * 86_400


def _within(candidate: Path, root: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=False)
        allowed = root.resolve(strict=False)
    except OSError:
        return False
    return resolved != allowed and allowed in resolved.parents


def plan_audio_prune(
    data_dir: Path, request: AudioPruneRequest, *, dry_run: bool = True
) -> AudioPrunePlan:
    """Resolve every prune candidate below the allowed audio roots without deleting anything."""
    data_dir = Path(data_dir)
    derived_root = data_dir / "derived" / "audio" / "clips"
    raw_root = data_dir / "raw" / "corpora"
    entries: list[AudioPruneEntry] = []
    orphaned: list[Path] = []
    languages = request.languages or None
    for video_dir in _caption_video_directories(data_dir, languages):
        language = video_dir.parent.name
        video_key = video_dir.name
        channel = _video_channel(video_dir)
        if not request.matches_video(language=language, video_key=video_key, channel=channel):
            continue
        clip_root = derived_root / language / video_key
        selected_clips: list[Path] = []
        remaining_clips: list[Path] = []
        if clip_root.is_dir():
            for clip_dir in sorted(path for path in clip_root.iterdir() if path.is_dir()):
                if clip_dir.is_symlink() or not _within(clip_dir, derived_root):
                    continue
                if CLIP_KEY_RE.fullmatch(clip_dir.name) is None:
                    if clip_dir.name.startswith(".") and _older_than_hours(
                        clip_dir, request.stale_temporary_hours
                    ):
                        entries.append(
                            AudioPruneEntry(
                                path=clip_dir,
                                kind="stale_temporary",
                                language=language,
                                video_key=video_key,
                                clip_key=None,
                                bytes=_directory_bytes(clip_dir),
                            )
                        )
                    continue
                paths = prepared_clip_paths(
                    data_dir, language=language, video_key=video_key, clip_key=clip_dir.name
                )
                manifest = read_clip_manifest(paths)
                version = str(manifest.get("preparation_version")) if manifest else None
                if request.preparation_versions and version not in request.preparation_versions:
                    remaining_clips.append(clip_dir)
                    continue
                reference = paths.manifest if paths.manifest.is_file() else clip_dir
                if not _older_than(reference, request.older_than_days):
                    remaining_clips.append(clip_dir)
                    continue
                selected_clips.append(clip_dir)
                entries.append(
                    AudioPruneEntry(
                        path=clip_dir,
                        kind="derived_clip",
                        language=language,
                        video_key=video_key,
                        clip_key=clip_dir.name,
                        bytes=_directory_bytes(clip_dir),
                    )
                )
        if request.include_raw_audio:
            audio_dir = raw_audio_paths(data_dir, language=language, video_key=video_key).directory
            if (
                audio_dir.is_dir()
                and not audio_dir.is_symlink()
                and _within(audio_dir, raw_root)
                and _older_than(
                    audio_dir / "manifest.json"
                    if (audio_dir / "manifest.json").is_file()
                    else audio_dir,
                    request.older_than_days,
                )
            ):
                entries.append(
                    AudioPruneEntry(
                        path=audio_dir,
                        kind="raw_audio",
                        language=language,
                        video_key=video_key,
                        clip_key=None,
                        bytes=_directory_bytes(audio_dir),
                    )
                )
                orphaned.extend(remaining_clips)
        for staged in sorted(video_dir.glob(".audio-*")):
            if (
                staged.is_dir()
                and not staged.is_symlink()
                and _within(staged, raw_root)
                and _older_than_hours(staged, request.stale_temporary_hours)
            ):
                entries.append(
                    AudioPruneEntry(
                        path=staged,
                        kind="stale_temporary",
                        language=language,
                        video_key=video_key,
                        clip_key=None,
                        bytes=_directory_bytes(staged),
                    )
                )
    if not dry_run:
        removed: list[AudioPruneEntry] = []
        for entry in entries:
            shutil.rmtree(entry.path, ignore_errors=True)
            parent = entry.path.parent
            if entry.kind == "derived_clip":
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
            removed.append(
                AudioPruneEntry(
                    path=entry.path,
                    kind=entry.kind,
                    language=entry.language,
                    video_key=entry.video_key,
                    clip_key=entry.clip_key,
                    bytes=entry.bytes,
                    deleted=not entry.path.exists(),
                )
            )
        entries = removed
    return AudioPrunePlan(
        generated_at=_now(),
        dry_run=dry_run,
        entries=tuple(entries),
        orphaned_clips=tuple(orphaned),
        derived_clips=sum(item.kind == "derived_clip" for item in entries),
        derived_bytes=sum(item.bytes for item in entries if item.kind == "derived_clip"),
        raw_videos=sum(item.kind == "raw_audio" for item in entries),
        raw_bytes=sum(item.bytes for item in entries if item.kind == "raw_audio"),
        stale_temporary=sum(item.kind == "stale_temporary" for item in entries),
        stale_temporary_bytes=sum(item.bytes for item in entries if item.kind == "stale_temporary"),
    )


def execute_audio_prune(data_dir: Path, request: AudioPruneRequest) -> AudioPrunePlan:
    """Delete exactly the candidates that ``plan_audio_prune`` reports."""
    return plan_audio_prune(data_dir, request, dry_run=False)


def audio_cache_status(
    data_dir: Path,
    *,
    enabled: bool = False,
    languages: Sequence[str] | None = None,
    summary: AudioStorageSummary | None = None,
) -> AudioCacheStatus:
    """Project the storage scan onto the additive public status contract."""
    scan = summary if summary is not None else audio_storage(data_dir, languages=languages)
    return AudioCacheStatus(
        enabled=enabled,
        videos=scan.videos,
        ready=scan.ready,
        missing=scan.missing,
        failed=scan.failed,
        raw_bytes=scan.raw_bytes,
        derived_bytes=scan.derived_bytes,
        derived_clips=scan.derived_clips,
        issues=len(scan.issues),
        languages=[
            AudioLanguageStatus(
                source_language=item.language,
                videos=item.videos,
                ready=item.ready,
                missing=item.missing,
                failed=item.failed,
                raw_bytes=item.raw_bytes,
                derived_bytes=item.derived_bytes,
                derived_clips=item.derived_clips,
                channels=[
                    AudioChannelStatus(
                        channel=channel.channel,
                        videos=channel.videos,
                        ready=channel.ready,
                        missing=channel.missing,
                        failed=channel.failed,
                        raw_bytes=channel.raw_bytes,
                        derived_bytes=channel.derived_bytes,
                        derived_clips=channel.derived_clips,
                    )
                    for channel in item.channels
                ],
            )
            for item in scan.languages
        ],
    )
