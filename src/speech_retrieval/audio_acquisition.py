"""Opt-in acquisition of one audio-only provider representation per video.

The downloaded representation is kept exactly as the provider delivered it, so the file
stays an immutable input rather than a local transcode. Deriving analysis clips from it is
the separate concern of :mod:`speech_retrieval.audio`.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .audio import (
    AUDIO_MANIFEST_SCHEMA_VERSION,
    AudioProbe,
    AudioProbeError,
    RawAudioPaths,
    RawAudioRecord,
    audio_availability,
    ffprobe_runner,
    probe_audio,
    raw_audio_paths,
    tool_version,
)
from .catalogue import canonical_language

AUDIO_FORMAT_POLICY_VERSION = "smallest-audio-only-v1"
MAX_RECORDED_ATTEMPTS = 5
URL_RE = re.compile(r"https?://\S+")
MediaRunner = Callable[[Sequence[str]], str]
AcquisitionStage = Literal["selection", "download", "probe", "validation"]


class AudioAcquisitionError(RuntimeError):
    """Raised when audio for one video cannot be acquired."""

    def __init__(
        self,
        message: str,
        *,
        stage: AcquisitionStage = "download",
        code: str = "audio_acquisition_failed",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AudioFormatPolicy:
    """A versioned, documented rule for choosing one audio-only representation."""

    version: str = AUDIO_FORMAT_POLICY_VERSION
    minimum_bitrate_kbps: float = 48.0
    minimum_sample_rate: int = 16_000
    allowed_codecs: tuple[str, ...] = (
        "opus",
        "aac",
        "mp4a",
        "vorbis",
        "mp3",
        "flac",
        "alac",
        "ac-3",
        "ec-3",
    )
    codec_preference: tuple[str, ...] = ("opus", "mp4a", "aac", "vorbis", "mp3")

    def constraints(self) -> dict[str, Any]:
        return {
            "policy_version": self.version,
            "audio_only": True,
            "reject_drm": True,
            "minimum_bitrate_kbps": self.minimum_bitrate_kbps,
            "minimum_sample_rate": self.minimum_sample_rate,
            "allowed_codecs": list(self.allowed_codecs),
            "codec_preference": list(self.codec_preference),
            "prefer_progressive_download": True,
            "selection_rule": (
                "smallest advertised or approximate file size among candidates meeting the "
                "speech-quality floor; when no size is advertised, the lowest bitrate above "
                "the floor with a deterministic codec and format-id tiebreak"
            ),
        }


@dataclass(frozen=True, slots=True)
class AudioFormatCandidate:
    format_id: str
    ext: str | None
    acodec: str | None
    codec_family: str | None
    abr: float | None
    asr: int | None
    audio_channels: int | None
    filesize: int | None
    filesize_approx: int | None
    protocol: str | None
    fragmented: bool
    language: str | None
    format_note: str | None
    rejected: str | None = None

    @property
    def known_bytes(self) -> int | None:
        return self.filesize if self.filesize is not None else self.filesize_approx

    def payload(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id,
            "ext": self.ext,
            "acodec": self.acodec,
            "codec_family": self.codec_family,
            "abr": self.abr,
            "asr": self.asr,
            "audio_channels": self.audio_channels,
            "filesize": self.filesize,
            "filesize_approx": self.filesize_approx,
            "protocol": self.protocol,
            "fragmented": self.fragmented,
            "language": self.language,
            "format_note": self.format_note,
            "rejected": self.rejected,
        }


@dataclass(frozen=True, slots=True)
class AudioFormatSelection:
    format_id: str
    candidate: AudioFormatCandidate
    reason: Literal["smallest_known_size", "quality_fallback"]
    policy_version: str
    constraints: dict[str, Any] = field(default_factory=dict)
    considered: tuple[AudioFormatCandidate, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "constraints": self.constraints,
            "chosen": self.candidate.payload(),
            "considered": [item.payload() for item in self.considered],
        }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _codec_family(acodec: Any) -> str | None:
    if not isinstance(acodec, str) or not acodec or acodec == "none":
        return None
    return acodec.strip().lower().split(".", 1)[0]


def audio_format_candidates(
    info: dict[str, Any], *, policy: AudioFormatPolicy | None = None
) -> list[AudioFormatCandidate]:
    """Return every audio-only provider format with its policy verdict recorded."""
    policy = policy or AudioFormatPolicy()
    candidates: list[AudioFormatCandidate] = []
    for entry in info.get("formats") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("vcodec") != "none":
            continue
        format_id = entry.get("format_id")
        acodec = entry.get("acodec")
        family = _codec_family(acodec)
        abr = _number(entry.get("abr"))
        asr = _integer(entry.get("asr"))
        protocol = entry.get("protocol")
        fragmented = isinstance(protocol, str) and (
            protocol.startswith("m3u8") or "dash_segments" in protocol
        )
        rejected: str | None = None
        if not isinstance(format_id, str) or not format_id:
            rejected = "missing format_id"
        elif family is None:
            rejected = "no audio codec"
        elif entry.get("has_drm"):
            rejected = "digital restrictions"
        elif family not in policy.allowed_codecs:
            rejected = f"codec {family} is not allowed by the policy"
        elif abr is not None and abr < policy.minimum_bitrate_kbps:
            rejected = f"bitrate {abr} kbps is below the {policy.minimum_bitrate_kbps} floor"
        elif asr is not None and asr < policy.minimum_sample_rate:
            rejected = f"sample rate {asr} Hz is below the {policy.minimum_sample_rate} floor"
        candidates.append(
            AudioFormatCandidate(
                format_id=str(format_id) if format_id else "",
                ext=(
                    entry.get("audio_ext")
                    if entry.get("audio_ext") not in {None, "none"}
                    else entry.get("ext")
                ),
                acodec=acodec if isinstance(acodec, str) else None,
                codec_family=family,
                abr=abr,
                asr=asr,
                audio_channels=_integer(entry.get("audio_channels")),
                filesize=_integer(entry.get("filesize")),
                filesize_approx=_integer(entry.get("filesize_approx")),
                protocol=protocol if isinstance(protocol, str) else None,
                fragmented=fragmented,
                language=entry.get("language") if isinstance(entry.get("language"), str) else None,
                format_note=(
                    entry.get("format_note") if isinstance(entry.get("format_note"), str) else None
                ),
                rejected=rejected,
            )
        )
    return candidates


def select_audio_format(
    info: dict[str, Any], *, policy: AudioFormatPolicy | None = None
) -> AudioFormatSelection:
    """Choose one audio-only representation under an explicit, recorded policy."""
    policy = policy or AudioFormatPolicy()
    considered = audio_format_candidates(info, policy=policy)
    eligible = [item for item in considered if item.rejected is None]
    if not eligible:
        raise AudioAcquisitionError(
            "no audio-only provider format satisfies the acquisition policy",
            stage="selection",
            code="no_audio_format",
            retryable=False,
        )
    preference = {name: index for index, name in enumerate(policy.codec_preference)}

    def codec_rank(candidate: AudioFormatCandidate) -> int:
        return preference.get(candidate.codec_family or "", len(preference))

    sized = [item for item in eligible if item.known_bytes is not None]
    if sized:
        chosen = min(
            sized,
            key=lambda item: (
                item.fragmented,
                item.known_bytes or 0,
                item.abr if item.abr is not None else float("inf"),
                codec_rank(item),
                item.format_id,
            ),
        )
        reason: Literal["smallest_known_size", "quality_fallback"] = "smallest_known_size"
    else:
        chosen = min(
            eligible,
            key=lambda item: (
                item.fragmented,
                item.abr if item.abr is not None else float("inf"),
                codec_rank(item),
                item.format_id,
            ),
        )
        reason = "quality_fallback"
    return AudioFormatSelection(
        format_id=chosen.format_id,
        candidate=chosen,
        reason=reason,
        policy_version=policy.version,
        constraints=policy.constraints(),
        considered=tuple(considered),
    )


@dataclass(frozen=True, slots=True)
class AudioAcquisitionResult:
    """Independent audio outcome for one video; never a caption status."""

    language: str
    video_key: str
    video_id: str
    status: Literal["downloaded", "cached", "failed"]
    record: RawAudioRecord | None = None
    format_id: str | None = None
    size_bytes: int | None = None
    duration: float | None = None
    error: str | None = None
    error_code: str | None = None
    attempts: tuple[dict[str, Any], ...] = ()
    manifest: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status in {"downloaded", "cached"}


def _sanitize(message: str, *, limit: int = 300) -> str:
    cleaned = URL_RE.sub("<url>", " ".join(str(message).split()))
    return cleaned[:limit]


RETRYABLE_RE = re.compile(
    r"(timed out|timeout|temporary failure|connection|network|429|too many requests"
    r"|http error 5\d\d|unable to download|read error|incomplete|reset by peer)",
    re.IGNORECASE,
)
PERMANENT_RE = re.compile(
    r"(private video|video unavailable|removed by the uploader|copyright|drm"
    r"|members-only|age.restricted|sign in to confirm|not available in your country)",
    re.IGNORECASE,
)


def classify_failure(message: str) -> tuple[str, bool]:
    """Return an error code and whether the failure is worth retrying."""
    text = str(message)
    if PERMANENT_RE.search(text):
        return "provider_rejected", False
    if RETRYABLE_RE.search(text):
        return "provider_temporary", True
    return "audio_acquisition_failed", False


def _download_arguments(
    *, format_id: str, url: str, output_template: Path, retries: int
) -> list[str]:
    return [
        "-f",
        format_id,
        "--no-playlist",
        "--no-overwrites",
        "--no-part",
        "--no-warnings",
        "--retries",
        str(retries),
        "--fragment-retries",
        str(retries),
        "--file-access-retries",
        str(retries),
        "--retry-sleep",
        "exp=1:20",
        "-o",
        str(output_template),
        url,
    ]


def _staged_media_file(staging: Path) -> Path:
    ignored = {".json", ".part", ".ytdl", ".temp", ".tmp"}
    files = sorted(
        item
        for item in staging.iterdir()
        if item.is_file() and not item.is_symlink() and item.suffix.lower() not in ignored
    )
    if len(files) != 1:
        raise AudioAcquisitionError(
            f"expected exactly one downloaded media file, found {len(files)}",
            stage="download",
            code="unexpected_download_output",
            retryable=False,
        )
    return files[0]


def _attempt_record(
    *, stage: str, message: str, code: str, retryable: bool, exhausted: bool
) -> dict[str, Any]:
    return {
        "attempted_at": datetime.now(UTC).isoformat(),
        "stage": stage,
        "error_code": code,
        "message": _sanitize(message),
        "retryable": retryable,
        "exhausted": exhausted,
    }


def _manifest_base(
    *,
    language: str,
    video_key: str,
    video_id: str,
    provider: str,
    selection: AudioFormatSelection | None,
) -> dict[str, Any]:
    return {
        "audio_manifest_schema_version": AUDIO_MANIFEST_SCHEMA_VERSION,
        "artifact": "raw_audio",
        "provider": provider,
        "source_language": language,
        "video_key": video_key,
        "video_id": video_id,
        "format_selection": selection.payload() if selection is not None else None,
    }


def _previous_attempts(record: RawAudioRecord) -> list[dict[str, Any]]:
    manifest = record.manifest or {}
    attempts = manifest.get("attempts")
    if not isinstance(attempts, list):
        return []
    return [item for item in attempts if isinstance(item, dict)][-MAX_RECORDED_ATTEMPTS:]


def _write_failure(paths: RawAudioPaths, payload: dict[str, Any]) -> None:
    paths.directory.mkdir(parents=True, exist_ok=True)
    temporary = paths.manifest.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(paths.manifest)


def acquire_audio(
    *,
    data_dir: Path,
    language: str,
    video_key: str,
    video_id: str,
    url: str,
    info: dict[str, Any],
    runner: MediaRunner,
    provider: str = "youtube",
    policy: AudioFormatPolicy | None = None,
    ffprobe: str = "ffprobe",
    probe_runner: Any = None,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    provider_retries: int = 3,
    yt_dlp_version: str | None = None,
    duration_tolerance_seconds: float = 2.0,
) -> AudioAcquisitionResult:
    """Acquire, validate, and publish one video's audio-only source representation.

    A valid cached payload is reused without any network call. Transient provider failures
    are retried with bounded backoff; every failure is recorded in an independent audio
    manifest so caption usability and indexing are never affected.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    language = canonical_language(language)
    paths = raw_audio_paths(data_dir, language=language, video_key=video_key)
    cached = audio_availability(
        data_dir, language=language, video_key=video_key, verify_checksum=True
    )
    if cached.ready:
        return AudioAcquisitionResult(
            language=language,
            video_key=video_key,
            video_id=video_id,
            status="cached",
            record=cached,
            format_id=cached.provider_format_id,
            size_bytes=cached.size_bytes,
            duration=cached.duration,
            manifest=cached.manifest,
        )
    history = _previous_attempts(cached)
    probe = probe_runner if probe_runner is not None else ffprobe_runner(ffprobe)
    selection: AudioFormatSelection | None = None

    def failure(message: str, *, stage: str, code: str, retryable: bool) -> AudioAcquisitionResult:
        history.append(
            _attempt_record(
                stage=stage,
                message=message,
                code=code,
                retryable=retryable,
                exhausted=True,
            )
        )
        payload = {
            **_manifest_base(
                language=language,
                video_key=video_key,
                video_id=video_id,
                provider=provider,
                selection=selection,
            ),
            "status": "failed",
            "raw_audio": None,
            "attempts": history[-MAX_RECORDED_ATTEMPTS:],
            "failed_at": datetime.now(UTC).isoformat(),
        }
        for stale in paths.directory.glob("source.*"):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink(missing_ok=True)
        _write_failure(paths, payload)
        return AudioAcquisitionResult(
            language=language,
            video_key=video_key,
            video_id=video_id,
            status="failed",
            error=_sanitize(message),
            error_code=code,
            attempts=tuple(payload["attempts"]),
            manifest=payload,
        )

    try:
        selection = select_audio_format(info, policy=policy)
    except AudioAcquisitionError as error:
        return failure(str(error), stage=error.stage, code=error.code, retryable=error.retryable)

    provider_duration = _number(info.get("duration"))
    video_directory = paths.directory.parent
    video_directory.mkdir(parents=True, exist_ok=True)
    for index in range(1, attempts + 1):
        staging = Path(tempfile.mkdtemp(prefix=".audio-", dir=video_directory))
        stage: str = "download"
        try:
            runner(
                _download_arguments(
                    format_id=selection.format_id,
                    url=url,
                    output_template=staging / "source.%(ext)s",
                    retries=provider_retries,
                )
            )
            media = _staged_media_file(staging)
            extension = media.suffix.lstrip(".").lower()
            target_name = paths.source(extension).name
            if media.name != target_name:
                media = media.replace(staging / target_name)
            stage = "probe"
            audio_probe: AudioProbe = probe_audio(media, runner=probe)
            stage = "validation"
            if provider_duration is not None:
                tolerance = max(duration_tolerance_seconds, provider_duration * 0.02)
                if abs(audio_probe.duration - provider_duration) > tolerance:
                    raise AudioAcquisitionError(
                        "downloaded audio duration "
                        f"{audio_probe.duration:.3f}s does not match the provider duration "
                        f"{provider_duration:.3f}s",
                        stage="validation",
                        code="duration_mismatch",
                        retryable=True,
                    )
            manifest = {
                **_manifest_base(
                    language=language,
                    video_key=video_key,
                    video_id=video_id,
                    provider=provider,
                    selection=selection,
                ),
                "status": "ready",
                "raw_audio": {
                    "filename": target_name,
                    "extension": extension,
                    "provider_format_id": selection.format_id,
                    "provider_ext": selection.candidate.ext,
                    "provider_acodec": selection.candidate.acodec,
                    "provider_abr": selection.candidate.abr,
                    "provider_asr": selection.candidate.asr,
                    "provider_audio_channels": selection.candidate.audio_channels,
                    "provider_filesize": selection.candidate.filesize,
                    "provider_filesize_approx": selection.candidate.filesize_approx,
                    "provider_protocol": selection.candidate.protocol,
                    "provider_duration": provider_duration,
                    "provider_license": info.get("license"),
                    "size_bytes": audio_probe.size_bytes,
                    "content_sha256": audio_probe.content_sha256,
                    "duration": audio_probe.duration,
                    "format_name": audio_probe.format_name,
                    "codec_name": audio_probe.codec_name,
                    "sample_rate": audio_probe.sample_rate,
                    "channels": audio_probe.channels,
                    "bit_rate": audio_probe.bit_rate,
                },
                "tool_versions": {
                    "yt_dlp": yt_dlp_version,
                    "ffprobe": tool_version(ffprobe),
                },
                "attempts": history[-MAX_RECORDED_ATTEMPTS:],
                "acquired_at": datetime.now(UTC).isoformat(),
            }
            (staging / paths.manifest.name).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if paths.directory.exists():
                shutil.rmtree(paths.directory, ignore_errors=True)
            staging.replace(paths.directory)
            staging = paths.directory
            record = audio_availability(data_dir, language=language, video_key=video_key)
            return AudioAcquisitionResult(
                language=language,
                video_key=video_key,
                video_id=video_id,
                status="downloaded",
                record=record,
                format_id=selection.format_id,
                size_bytes=audio_probe.size_bytes,
                duration=audio_probe.duration,
                attempts=tuple(history[-MAX_RECORDED_ATTEMPTS:]),
                manifest=manifest,
            )
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            if isinstance(error, AudioAcquisitionError):
                code, retryable = error.code, error.retryable
                stage = error.stage
            elif isinstance(error, AudioProbeError):
                code, retryable = "invalid_media", False
            else:
                code, retryable = classify_failure(str(error))
            if not retryable or index == attempts:
                return failure(str(error), stage=stage, code=code, retryable=retryable)
            history.append(
                _attempt_record(
                    stage=stage,
                    message=str(error),
                    code=code,
                    retryable=True,
                    exhausted=False,
                )
            )
            sleep(backoff_seconds * (2 ** (index - 1)))
    raise AssertionError("unreachable: the retry loop always returns")


def yt_dlp_version() -> str | None:
    """Return the installed yt-dlp version without importing the downloader eagerly."""
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        try:
            from yt_dlp.version import __version__
        except ImportError:
            return None
        return str(__version__) or None


def record_audio_failure(
    data_dir: Path,
    *,
    language: str,
    video_key: str,
    video_id: str,
    stage: AcquisitionStage,
    message: str,
    code: str,
    retryable: bool = False,
    provider: str = "youtube",
) -> AudioAcquisitionResult:
    """Persist a failure that happened before format selection could even be attempted."""
    language = canonical_language(language)
    paths = raw_audio_paths(data_dir, language=language, video_key=video_key)
    previous = _previous_attempts(
        audio_availability(data_dir, language=language, video_key=video_key)
    )
    previous.append(
        _attempt_record(
            stage=stage, message=message, code=code, retryable=retryable, exhausted=True
        )
    )
    payload = {
        **_manifest_base(
            language=language,
            video_key=video_key,
            video_id=video_id,
            provider=provider,
            selection=None,
        ),
        "status": "failed",
        "raw_audio": None,
        "attempts": previous[-MAX_RECORDED_ATTEMPTS:],
        "failed_at": datetime.now(UTC).isoformat(),
    }
    _write_failure(paths, payload)
    return AudioAcquisitionResult(
        language=language,
        video_key=video_key,
        video_id=video_id,
        status="failed",
        error=_sanitize(message),
        error_code=code,
        attempts=tuple(payload["attempts"]),
        manifest=payload,
    )
