from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalogue import canonical_language
from .identity import clip_id

AUDIO_PREPARATION_VERSION = "pcm-s16le-mono-16000-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
VIDEO_KEY_RE = re.compile(r"vid_[0-9a-f]{20}")
CLIP_KEY_RE = re.compile(r"clp_[0-9a-f]{20}")


class AudioProbeError(RuntimeError):
    pass


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


def _run_ffprobe(arguments: Sequence[str]) -> str:
    result = subprocess.run(
        ["ffprobe", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed"
        raise AudioProbeError(f"ffprobe {message}")
    return result.stdout


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
