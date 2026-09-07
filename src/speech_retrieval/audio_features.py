"""Independently optional acoustic features with one shared result envelope.

Every helper returns a :class:`FeatureResult` keyed by stable segment ID and clip identity,
so a later ranking stage consumes values by ``(segment_id, feature, version)`` and never by
file path. A missing dependency or missing media produces ``unavailable`` and an inference or
validation exception produces ``failed``; neither ever becomes a numeric zero.

Heavy machine-learning imports happen only inside the adapter that needs them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .audio_scoring import DisagreementScore

FEATURE_SCHEMA_VERSION = 1
CAPTION_AGREEMENT_FEATURE = "caption_asr_agreement"
CAPTION_AGREEMENT_VERSION = "caption-asr-agreement-v1"
SPEAKING_RATE_FEATURE = "speaking_rate"
SPEAKING_RATE_VERSION = "speaking-rate-v1"
SPEECH_RATIO_FEATURE = "speech_ratio"
SPEECH_RATIO_VERSION = "silero-speech-ratio-v1"
SQUIM_OBJECTIVE_FEATURE = "squim_objective"
SQUIM_OBJECTIVE_VERSION = "squim-objective-v1"
SQUIM_SUBJECTIVE_FEATURE = "squim_subjective"
SQUIM_SUBJECTIVE_VERSION = "squim-subjective-v1"

FeatureStatus = Literal["complete", "unavailable", "failed"]


@dataclass(frozen=True, slots=True)
class FeatureError:
    code: str
    message: str

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class FeatureResult:
    """The stable per-segment handoff a ranking stage can consume."""

    segment_id: str
    feature: str
    version: str
    status: FeatureStatus
    values: dict[str, float | int | None] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    clip_key: str | None = None
    clip_sha256: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    implementation: dict[str, Any] = field(default_factory=dict)
    runtime_ms: float | None = None
    error: FeatureError | None = None

    @property
    def usable(self) -> bool:
        return self.status == "complete"

    def payload(self) -> dict[str, Any]:
        return {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "segment_id": self.segment_id,
            "feature": self.feature,
            "version": self.version,
            "status": self.status,
            "values": self.values,
            "units": self.units,
            "clip_key": self.clip_key,
            "clip_sha256": self.clip_sha256,
            "input": self.input,
            "implementation": self.implementation,
            "runtime_ms": self.runtime_ms,
            "error": self.error.payload() if self.error is not None else None,
        }


class ClipLike(Protocol):
    """The read-only subset of a prepared clip every acoustic feature needs."""

    @property
    def path(self) -> Path: ...

    @property
    def clip_key(self) -> str: ...

    @property
    def content_sha256(self) -> str: ...

    @property
    def effective_start(self) -> float: ...

    @property
    def effective_end(self) -> float: ...

    @property
    def duration(self) -> float: ...

    @property
    def sample_rate(self) -> int: ...

    @property
    def channels(self) -> int: ...


def _clip_provenance(clip: ClipLike | None) -> dict[str, Any]:
    if clip is None:
        return {}
    return {
        "clip_key": clip.clip_key,
        "clip_sha256": clip.content_sha256,
        "effective_start": clip.effective_start,
        "effective_end": clip.effective_end,
        "duration": clip.duration,
        "sample_rate": clip.sample_rate,
        "channels": clip.channels,
    }


def _result(
    segment_id: str,
    feature: str,
    version: str,
    status: FeatureStatus,
    *,
    clip: ClipLike | None = None,
    values: dict[str, float | int | None] | None = None,
    units: dict[str, str] | None = None,
    inputs: dict[str, Any] | None = None,
    implementation: dict[str, Any] | None = None,
    runtime_ms: float | None = None,
    error: FeatureError | None = None,
) -> FeatureResult:
    return FeatureResult(
        segment_id=segment_id,
        feature=feature,
        version=version,
        status=status,
        values=values or {},
        units=units or {},
        clip_key=clip.clip_key if clip is not None else None,
        clip_sha256=clip.content_sha256 if clip is not None else None,
        input={**_clip_provenance(clip), **(inputs or {})},
        implementation=implementation or {},
        runtime_ms=runtime_ms,
        error=error,
    )


def caption_agreement(
    segment_id: str,
    score: DisagreementScore,
    *,
    clip: ClipLike | None = None,
    version: str = CAPTION_AGREEMENT_VERSION,
) -> FeatureResult:
    """Reuse the scorer output as a per-segment transcript-trustworthiness estimate.

    Both the unbounded metric family (``wer``/``cer``) and the normalization version are
    named, so a later consumer cannot mistake this for a confirmed caption-error label.
    """
    implementation = {
        "metric": score.metric,
        "unit": score.unit,
        "normalization_version": score.normalization_version,
        "orientation": "reference=normalized ASR, hypothesis=normalized caption",
        "interpretation": "disagreement with a declared reference, not confirmed caption error",
    }
    if score.error_rate is None:
        return _result(
            segment_id,
            CAPTION_AGREEMENT_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(
                code="empty_reference",
                message="the ASR reference normalized to zero tokens, so no rate has a denominator",
            ),
        )
    return _result(
        segment_id,
        CAPTION_AGREEMENT_FEATURE,
        version,
        "complete",
        clip=clip,
        values={
            "error_rate": score.error_rate,
            "agreement": score.agreement,
            "substitutions": score.substitutions,
            "deletions": score.deletions,
            "insertions": score.insertions,
            "hits": score.hits,
            "reference_length": score.reference_length,
            "hypothesis_length": score.hypothesis_length,
        },
        units={
            "error_rate": "ratio_of_reference_units",
            "agreement": "bounded_ratio",
            "reference_length": score.unit + "s",
            "hypothesis_length": score.unit + "s",
        },
        implementation=implementation,
    )


def speaking_rate(
    segment_id: str,
    *,
    caption_tokens: int,
    asr_tokens: int,
    clip_duration: float,
    speech_seconds: float | None = None,
    clip: ClipLike | None = None,
    tokenizer_version: str,
    language: str,
    version: str = SPEAKING_RATE_VERSION,
) -> FeatureResult:
    """Record caption and ASR token rates with explicit numerators and denominators.

    Rates over voiced seconds and over clip seconds are both retained: the first is the
    meaningful speed estimate, the second is needed to diagnose bad clip windows. Token
    rates are not comparable across languages, which the provenance states.
    """
    if clip_duration <= 0:
        return _result(
            segment_id,
            SPEAKING_RATE_FEATURE,
            version,
            "failed",
            clip=clip,
            error=FeatureError(code="invalid_duration", message="clip duration must be positive"),
        )
    values: dict[str, float | int | None] = {
        "caption_token_count": caption_tokens,
        "asr_token_count": asr_tokens,
        "clip_duration": clip_duration,
        "speech_seconds": speech_seconds,
        "caption_tokens_per_clip_second": caption_tokens / clip_duration,
        "asr_tokens_per_clip_second": asr_tokens / clip_duration,
        "caption_tokens_per_speech_second": (
            caption_tokens / speech_seconds if speech_seconds and speech_seconds > 0 else None
        ),
        "asr_tokens_per_speech_second": (
            asr_tokens / speech_seconds if speech_seconds and speech_seconds > 0 else None
        ),
    }
    return _result(
        segment_id,
        SPEAKING_RATE_FEATURE,
        version,
        "complete",
        clip=clip,
        values=values,
        units={
            "caption_tokens_per_clip_second": "tokens_per_second",
            "asr_tokens_per_clip_second": "tokens_per_second",
            "caption_tokens_per_speech_second": "tokens_per_second",
            "asr_tokens_per_speech_second": "tokens_per_second",
            "clip_duration": "seconds",
            "speech_seconds": "seconds",
        },
        implementation={
            "tokenizer_version": tokenizer_version,
            "language": language,
            "denominator": "voiced seconds when available, clip seconds otherwise",
            "cross_language_comparable": False,
        },
    )


@dataclass(frozen=True, slots=True)
class SileroSettings:
    """Silero VAD parameters. The defaults are not validated on this corpus."""

    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    speech_pad_ms: int = 30
    sampling_rate: int = 16_000
    backend: Literal["torch", "onnx"] = "torch"

    def payload(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "min_speech_duration_ms": self.min_speech_duration_ms,
            "min_silence_duration_ms": self.min_silence_duration_ms,
            "speech_pad_ms": self.speech_pad_ms,
            "sampling_rate": self.sampling_rate,
            "backend": self.backend,
        }


SpeechDetector = Callable[[Path, SileroSettings], Sequence[tuple[float, float]]]


def merge_intervals(
    intervals: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Merge overlapping speech intervals so voiced seconds are never double counted."""
    ordered = sorted(
        (float(start), float(end)) for start, end in intervals if float(end) > float(start)
    )
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def union_seconds(intervals: Sequence[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))


def _silero_detector(path: Path, settings: SileroSettings) -> Sequence[tuple[float, float]]:
    from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

    model = load_silero_vad(onnx=settings.backend == "onnx")
    waveform = read_audio(str(path), sampling_rate=settings.sampling_rate)
    stamps = get_speech_timestamps(
        waveform,
        model,
        sampling_rate=settings.sampling_rate,
        threshold=settings.threshold,
        min_speech_duration_ms=settings.min_speech_duration_ms,
        min_silence_duration_ms=settings.min_silence_duration_ms,
        speech_pad_ms=settings.speech_pad_ms,
        return_seconds=True,
    )
    return [(float(item["start"]), float(item["end"])) for item in stamps]


def speech_ratio(
    segment_id: str,
    clip: ClipLike,
    *,
    settings: SileroSettings | None = None,
    detector: SpeechDetector | None = None,
    version: str = SPEECH_RATIO_VERSION,
) -> FeatureResult:
    """Voiced-seconds share of the prepared clip, from a lightweight VAD."""
    settings = settings or SileroSettings()
    implementation = {"model": "silero-vad", "settings": settings.payload()}
    if clip.sample_rate != settings.sampling_rate or clip.channels != 1:
        return _result(
            segment_id,
            SPEECH_RATIO_FEATURE,
            version,
            "failed",
            clip=clip,
            implementation=implementation,
            error=FeatureError(
                code="unsupported_waveform",
                message="the VAD requires the prepared mono clip at its configured sample rate",
            ),
        )
    if not clip.path.is_file():
        return _result(
            segment_id,
            SPEECH_RATIO_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_media", message=f"clip is missing: {clip.path}"),
        )
    started = time.perf_counter()
    try:
        intervals = (detector or _silero_detector)(clip.path, settings)
    except ImportError as error:
        return _result(
            segment_id,
            SPEECH_RATIO_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_dependency", message=str(error)),
        )
    except Exception as error:
        return _result(
            segment_id,
            SPEECH_RATIO_FEATURE,
            version,
            "failed",
            clip=clip,
            implementation=implementation,
            runtime_ms=(time.perf_counter() - started) * 1000,
            error=FeatureError(code="inference_failed", message=str(error)),
        )
    merged = merge_intervals(intervals)
    voiced = union_seconds(merged)
    duration = clip.duration
    return _result(
        segment_id,
        SPEECH_RATIO_FEATURE,
        version,
        "complete",
        clip=clip,
        values={
            "speech_ratio": min(1.0, voiced / duration) if duration > 0 else None,
            "speech_seconds": voiced,
            "clip_duration": duration,
            "interval_count": len(merged),
        },
        units={
            "speech_ratio": "ratio_of_clip_duration",
            "speech_seconds": "seconds",
            "clip_duration": "seconds",
        },
        inputs={"speech_intervals": [list(item) for item in merged]},
        implementation=implementation,
        runtime_ms=(time.perf_counter() - started) * 1000,
    )


ObjectiveEstimator = Callable[[Path], dict[str, float]]
SubjectiveEstimator = Callable[[Path, Path], dict[str, float]]


@dataclass(frozen=True, slots=True)
class SubjectiveReference:
    """A fixed non-matching speech reference SQUIM subjective MOS requires."""

    path: Path
    source: str
    license: str
    content_sha256: str

    def payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source": self.source,
            "license": self.license,
            "content_sha256": self.content_sha256,
        }


def _squim_objective_estimator(path: Path) -> dict[str, float]:
    import torchaudio
    from torchaudio.pipelines import SQUIM_OBJECTIVE

    model = SQUIM_OBJECTIVE.get_model()
    waveform, sample_rate = torchaudio.load(str(path))
    if sample_rate != SQUIM_OBJECTIVE.sample_rate:
        raise ValueError(
            f"SQUIM objective expects {SQUIM_OBJECTIVE.sample_rate} Hz, got {sample_rate}"
        )
    stoi, pesq, si_sdr = model(waveform)
    return {
        "stoi": float(stoi.item()),
        "pesq": float(pesq.item()),
        "si_sdr": float(si_sdr.item()),
    }


def _squim_subjective_estimator(path: Path, reference: Path) -> dict[str, float]:
    import torchaudio
    from torchaudio.pipelines import SQUIM_SUBJECTIVE

    model = SQUIM_SUBJECTIVE.get_model()
    waveform, sample_rate = torchaudio.load(str(path))
    reference_waveform, reference_rate = torchaudio.load(str(reference))
    if sample_rate != SQUIM_SUBJECTIVE.sample_rate or reference_rate != sample_rate:
        raise ValueError("SQUIM subjective requires both inputs at the bundle sample rate")
    mos = model(waveform, reference_waveform)
    return {"mos": float(mos.item())}


def squim_objective(
    segment_id: str,
    clip: ClipLike,
    *,
    estimator: ObjectiveEstimator | None = None,
    version: str = SQUIM_OBJECTIVE_VERSION,
) -> FeatureResult:
    """Reference-free predicted STOI, PESQ, and SI-SDR for one prepared clip.

    SQUIM was trained to assess speech enhancement rather than spontaneous recordings, so
    each output must survive its own documented sample review before any ranking use.
    """
    implementation = {
        "model": "torchaudio SQUIM_OBJECTIVE",
        "outputs": ["stoi", "pesq", "si_sdr"],
        "reference_free": True,
        "training_domain": "speech enhancement, not spontaneous video audio",
    }
    if not clip.path.is_file():
        return _result(
            segment_id,
            SQUIM_OBJECTIVE_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_media", message=f"clip is missing: {clip.path}"),
        )
    started = time.perf_counter()
    try:
        values = (estimator or _squim_objective_estimator)(clip.path)
    except ImportError as error:
        return _result(
            segment_id,
            SQUIM_OBJECTIVE_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_dependency", message=str(error)),
        )
    except Exception as error:
        return _result(
            segment_id,
            SQUIM_OBJECTIVE_FEATURE,
            version,
            "failed",
            clip=clip,
            implementation=implementation,
            runtime_ms=(time.perf_counter() - started) * 1000,
            error=FeatureError(code="inference_failed", message=str(error)),
        )
    return _result(
        segment_id,
        SQUIM_OBJECTIVE_FEATURE,
        version,
        "complete",
        clip=clip,
        values={key: float(value) for key, value in values.items()},
        units={"stoi": "predicted_stoi", "pesq": "predicted_pesq", "si_sdr": "predicted_db"},
        implementation=implementation,
        runtime_ms=(time.perf_counter() - started) * 1000,
    )


def squim_subjective(
    segment_id: str,
    clip: ClipLike,
    *,
    reference: SubjectiveReference | None,
    estimator: SubjectiveEstimator | None = None,
    version: str = SQUIM_SUBJECTIVE_VERSION,
) -> FeatureResult:
    """Predicted MOS, which still needs a fixed non-matching speech reference.

    "Reference-free MOS" does not mean "no second input": without a defensible reference the
    value is reported unavailable rather than invented.
    """
    implementation = {
        "model": "torchaudio SQUIM_SUBJECTIVE",
        "outputs": ["mos"],
        "requires_non_matching_reference": True,
        "reference": reference.payload() if reference is not None else None,
    }
    if reference is None or not Path(reference.path).is_file():
        return _result(
            segment_id,
            SQUIM_SUBJECTIVE_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(
                code="missing_non_matching_reference",
                message="no fixed, licensed non-matching speech reference is configured",
            ),
        )
    if not clip.path.is_file():
        return _result(
            segment_id,
            SQUIM_SUBJECTIVE_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_media", message=f"clip is missing: {clip.path}"),
        )
    started = time.perf_counter()
    try:
        values = (estimator or _squim_subjective_estimator)(clip.path, Path(reference.path))
    except ImportError as error:
        return _result(
            segment_id,
            SQUIM_SUBJECTIVE_FEATURE,
            version,
            "unavailable",
            clip=clip,
            implementation=implementation,
            error=FeatureError(code="missing_dependency", message=str(error)),
        )
    except Exception as error:
        return _result(
            segment_id,
            SQUIM_SUBJECTIVE_FEATURE,
            version,
            "failed",
            clip=clip,
            implementation=implementation,
            runtime_ms=(time.perf_counter() - started) * 1000,
            error=FeatureError(code="inference_failed", message=str(error)),
        )
    return _result(
        segment_id,
        SQUIM_SUBJECTIVE_FEATURE,
        version,
        "complete",
        clip=clip,
        values={key: float(value) for key, value in values.items()},
        units={"mos": "predicted_mos"},
        implementation=implementation,
        runtime_ms=(time.perf_counter() - started) * 1000,
    )
