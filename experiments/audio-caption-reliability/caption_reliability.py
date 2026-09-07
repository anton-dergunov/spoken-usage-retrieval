"""Frozen configuration, deterministic sampling, and aggregation for Plan 09.

Everything here is pure and dependency-light so the schemas, the sample, and the report
arithmetic can be tested offline. Provider, ASR, and model adapters live in ``run.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from speech_retrieval.audio_scoring import SCORING_NORMALIZERS

CONFIG_VERSION = 1
RESULT_SCHEMA_VERSION = 1
SAMPLING_ALGORITHM = "sha256-hash-order-v1"
RUBRIC_VERSION = "caption-review-v1"
RECOMMENDATIONS = (
    "use_directly",
    "attach_score",
    "verify_selectively",
    "replace_with_asr",
    "collect_more_evidence",
)
ACOUSTIC_VOCABULARY = (
    "clean_single_speaker",
    "background_music_or_noise",
    "overlapping_speakers",
    "distant_or_reverberant",
    "fast_speech",
)
REVIEW_TAGS = (
    "meaning_change",
    "omitted_speech",
    "extra_speech",
    "name_or_entity_error",
    "disfluency_difference",
    "punctuation_only_difference",
    "boundary_problem",
    "start_cut",
    "end_cut",
    "overlap",
    "noise_or_music",
    "unclear_speech",
)
RowStatus = Literal[
    "pending",
    "complete",
    "missing_audio",
    "download_failed",
    "clip_failed",
    "asr_failed",
    "scoring_failed",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stratum(Model):
    """A predeclared source class. Channel and acoustics are breakdowns, not classes."""

    id: str
    source_language: str
    caption_provenance: Literal["authored", "automatic"]
    requested: int = Field(ge=1)


class Sampling(Model):
    seed: int
    algorithm: Literal["sha256-hash-order-v1"] = "sha256-hash-order-v1"
    inventory: Literal["indexed_segments"] = "indexed_segments"
    per_video_cap: int = Field(ge=1)
    minimum_clip_seconds: float = Field(gt=0)
    maximum_clip_seconds: float = Field(gt=0)
    minimum_tokens: int = Field(ge=1)
    minimum_gap_seconds: float = Field(ge=0)
    strata: list[Stratum] = Field(min_length=1)

    @property
    def requested_total(self) -> int:
        return sum(item.requested for item in self.strata)


class AudioSettings(Model):
    use_segment_clip_range: bool = True
    padding_seconds: float = Field(default=0.0, ge=0)
    preparation_version: str
    duration_tolerance_ms: int = Field(default=50, ge=0)


class AsrSettings(Model):
    """Frozen reference settings; backend defaults are never inherited implicitly."""

    backend: Literal["faster-whisper"] = "faster-whisper"
    model: str
    revision: str | None = None
    device: str = "auto"
    compute_type: str = "default"
    cpu_threads: int = Field(default=0, ge=0)
    num_workers: int = Field(default=1, ge=1)
    language: str
    task: Literal["transcribe"] = "transcribe"
    beam_size: int = Field(ge=1)
    best_of: int = Field(ge=1)
    patience: float = Field(gt=0)
    temperature: list[float] = Field(min_length=1)
    condition_on_previous_text: bool = False
    vad_filter: bool = False
    word_timestamps: bool = True
    initial_prompt: None = Field(
        default=None,
        description="Must stay null: priming with the tested caption would bias agreement upward.",
    )


class ScoringSettings(Model):
    orientation: Literal["reference=asr,hypothesis=caption"] = "reference=asr,hypothesis=caption"
    normalization: dict[str, str]
    sensitivity_normalization: dict[str, str] = Field(default_factory=dict)


class SileroConfig(Model):
    threshold: float = Field(ge=0, le=1)
    min_speech_duration_ms: int = Field(ge=0)
    min_silence_duration_ms: int = Field(ge=0)
    speech_pad_ms: int = Field(ge=0)
    backend: Literal["torch", "onnx"] = "torch"


class SubjectiveReferenceConfig(Model):
    path: str
    source: str
    license: str
    content_sha256: str


class FeatureSettings(Model):
    caption_agreement: bool = True
    speaking_rate: bool = True
    speech_ratio: bool = True
    squim_objective: bool = True
    squim_subjective: bool = False
    silero: SileroConfig
    subjective_reference: SubjectiveReferenceConfig | None = None


class ReviewSettings(Model):
    rubric_version: str = RUBRIC_VERSION
    subset: Literal["all", "failed_plus_stratified_bins"] = "failed_plus_stratified_bins"
    disagreement_bins: list[list[float]] = Field(min_length=1)
    per_bin_per_stratum: int = Field(ge=1)
    duplicate_review_fraction: float = Field(default=0.1, ge=0, le=1)


class Authorization(Model):
    """A preflight gate the code cannot infer from public visibility."""

    required: bool = True
    confirmed: bool = False
    basis: str | None = None
    allowlist_path: str | None = None


class ExperimentConfig(Model):
    config_version: Literal[1] = 1
    experiment_id: Literal["audio-caption-reliability"] = "audio-caption-reliability"
    decision: str
    hypothesis: str
    acceptance_evidence: list[str] = Field(min_length=1)
    source_languages: list[str] = Field(min_length=1)
    source_classes: list[str] = Field(min_length=1)
    recommendations: list[str] = Field(default_factory=lambda: list(RECOMMENDATIONS))
    acoustic_vocabulary: list[str] = Field(default_factory=lambda: list(ACOUSTIC_VOCABULARY))
    review_tags: list[str] = Field(default_factory=lambda: list(REVIEW_TAGS))
    sampling: Sampling
    audio: AudioSettings
    asr: AsrSettings
    scoring: ScoringSettings
    features: FeatureSettings
    review: ReviewSettings
    authorization: Authorization

    def validated(self) -> ExperimentConfig:
        known = set(SCORING_NORMALIZERS)
        for language, version in self.scoring.normalization.items():
            if version not in known:
                raise ValueError(f"unknown normalization {version!r} for {language}")
        for language, version in self.scoring.sensitivity_normalization.items():
            if version not in known:
                raise ValueError(f"unknown sensitivity normalization {version!r} for {language}")
        declared = {item.id for item in self.sampling.strata}
        if declared != set(self.source_classes):
            raise ValueError("sampling strata and predeclared source classes must match")
        for language in {item.source_language for item in self.sampling.strata}:
            if language not in self.scoring.normalization:
                raise ValueError(f"no normalization is configured for {language}")
        if set(self.recommendations) != set(RECOMMENDATIONS):
            raise ValueError("the recommendation vocabulary is fixed by the plan")
        return self


class ScoreRecord(Model):
    metric: Literal["wer", "cer"]
    normalization_version: str
    error_rate: float | None
    agreement: float
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    reference_length: int
    hypothesis_length: int
    normalized_reference: str
    normalized_hypothesis: str
    alignment: list[dict[str, Any]] = Field(default_factory=list)


class ReviewRecord(Model):
    reviewer: str
    rubric_version: str
    reviewed_at: str
    caption_verdict: Literal["correct", "acceptable", "incorrect", "uncertain"]
    reference_assessment: Literal[
        "equivalent", "caption_better", "asr_better", "both_wrong", "uncertain"
    ]
    tags: list[str] = Field(default_factory=list)
    acoustic_tags: list[str] = Field(default_factory=list)
    note: str | None = None
    corrected_transcript: str | None = None


class ResultRow(Model):
    """One frozen sample row; identity never changes when a stage fails."""

    result_schema_version: Literal[1] = 1
    run_id: str
    stratum: str
    segment_id: str
    video_key: str
    video_id: str
    track_id: str
    source_language: str
    channel: str | None
    caption_provenance: Literal["authored", "automatic"]
    caption_kind: str
    caption_text: str
    quality_score: float | None = None
    boundary_reason: str | None = None
    boundary_confidence: float | None = None
    token_count: int | None = None
    speech_style: list[str] = Field(default_factory=list)
    requested_start: float
    requested_end: float
    effective_start: float | None = None
    effective_end: float | None = None
    padding_before: float | None = None
    padding_after: float | None = None
    clip_key: str | None = None
    clip_sha256: str | None = None
    preparation_version: str | None = None
    asr_text: str | None = None
    asr_provenance: dict[str, Any] | None = None
    asr_segments: list[dict[str, Any]] = Field(default_factory=list)
    asr_words: list[dict[str, Any]] = Field(default_factory=list)
    asr_runtime_ms: float | None = None
    reference_provenance: str = "asr"
    score: ScoreRecord | None = None
    sensitivity_score: ScoreRecord | None = None
    features: list[dict[str, Any]] = Field(default_factory=list)
    acoustic_tags: list[str] = Field(default_factory=list)
    review: ReviewRecord | None = None
    status: RowStatus = "pending"
    error: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    segment_id: str
    video_key: str
    video_id: str
    track_id: str
    source_language: str
    channel: str | None
    caption_provenance: Literal["authored", "automatic"]
    caption_kind: str
    text: str
    token_count: int
    start: float
    end: float
    clip_start: float
    clip_end: float
    quality_score: float | None
    boundary_reason: str | None
    boundary_confidence: float | None
    speech_style: tuple[str, ...] = ()

    @property
    def clip_seconds(self) -> float:
        return self.clip_end - self.clip_start


def order_key(seed: int, segment_id: str) -> str:
    """Stable hash ordering so selection never depends on Python iteration order."""
    return hashlib.sha256(f"{seed}\0{segment_id}".encode()).hexdigest()


def canonical_checksum(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def load_inventory(data_dir: Path, languages: Sequence[str]) -> list[Candidate]:
    """Join indexed segments with their source-track metadata for provenance and channel."""
    candidates: list[Candidate] = []
    for language in languages:
        segments_path = Path(data_dir) / "derived" / "corpora" / language / "segments.jsonl"
        if not segments_path.is_file():
            continue
        metadata_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        with segments_path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = (row["video_key"], row["track_id"])
                if key not in metadata_cache:
                    path = (
                        Path(data_dir)
                        / "raw"
                        / "corpora"
                        / language
                        / row["video_key"]
                        / row["track_id"]
                        / "metadata.json"
                    )
                    try:
                        metadata_cache[key] = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        metadata_cache[key] = None
                metadata = metadata_cache[key]
                if metadata is None:
                    continue
                caption_kind = str(metadata.get("caption_kind", ""))
                candidates.append(
                    Candidate(
                        segment_id=row["id"],
                        video_key=row["video_key"],
                        video_id=row["video_id"],
                        track_id=row["track_id"],
                        source_language=language,
                        channel=metadata.get("channel_config_id"),
                        caption_provenance=(
                            "authored" if caption_kind == "manual" else "automatic"
                        ),
                        caption_kind=caption_kind,
                        text=row["text"],
                        token_count=int(row.get("token_count") or 0),
                        start=float(row["start"]),
                        end=float(row["end"]),
                        clip_start=float(row["clip_start"]),
                        clip_end=float(row["clip_end"]),
                        quality_score=row.get("quality_score"),
                        boundary_reason=row.get("boundary_reason"),
                        boundary_confidence=row.get("boundary_confidence"),
                        speech_style=tuple(metadata.get("speech_style") or ()),
                    )
                )
    return candidates


def stratum_of(candidate: Candidate, sampling: Sampling) -> Stratum | None:
    for stratum in sampling.strata:
        if (
            stratum.source_language == candidate.source_language
            and stratum.caption_provenance == candidate.caption_provenance
        ):
            return stratum
    return None


def filter_candidates(
    candidates: Iterable[Candidate], sampling: Sampling
) -> tuple[list[Candidate], dict[str, int]]:
    """Apply the declared eligibility rules and report the count lost at every step."""
    funnel = {
        "candidates": 0,
        "rejected_unknown_stratum": 0,
        "rejected_empty_text": 0,
        "rejected_too_few_tokens": 0,
        "rejected_too_short": 0,
        "rejected_too_long": 0,
        "eligible": 0,
    }
    eligible: list[Candidate] = []
    for candidate in candidates:
        funnel["candidates"] += 1
        if stratum_of(candidate, sampling) is None:
            funnel["rejected_unknown_stratum"] += 1
        elif not candidate.text.strip():
            funnel["rejected_empty_text"] += 1
        elif candidate.token_count < sampling.minimum_tokens:
            funnel["rejected_too_few_tokens"] += 1
        elif candidate.clip_seconds < sampling.minimum_clip_seconds:
            funnel["rejected_too_short"] += 1
        elif candidate.clip_seconds > sampling.maximum_clip_seconds:
            funnel["rejected_too_long"] += 1
        else:
            eligible.append(candidate)
    funnel["eligible"] = len(eligible)
    return eligible, funnel


def select_sample(
    candidates: Iterable[Candidate], sampling: Sampling
) -> tuple[list[Candidate], dict[str, Any]]:
    """Deterministically pick within-stratum segments under per-video and spacing caps."""
    eligible, funnel = filter_candidates(candidates, sampling)
    ordered = sorted(eligible, key=lambda item: order_key(sampling.seed, item.segment_id))
    selected: list[Candidate] = []
    per_video: dict[str, int] = {}
    per_stratum: dict[str, int] = {}
    chosen_spans: dict[str, list[tuple[float, float]]] = {}
    rejected_neighbours = 0
    rejected_video_cap = 0
    for candidate in ordered:
        stratum = stratum_of(candidate, sampling)
        if stratum is None:
            continue
        if per_stratum.get(stratum.id, 0) >= stratum.requested:
            continue
        if per_video.get(candidate.video_key, 0) >= sampling.per_video_cap:
            rejected_video_cap += 1
            continue
        spans = chosen_spans.setdefault(candidate.video_key, [])
        gap = sampling.minimum_gap_seconds
        if any(
            candidate.clip_start < end + gap and start - gap < candidate.clip_end
            for start, end in spans
        ):
            rejected_neighbours += 1
            continue
        spans.append((candidate.clip_start, candidate.clip_end))
        per_video[candidate.video_key] = per_video.get(candidate.video_key, 0) + 1
        per_stratum[stratum.id] = per_stratum.get(stratum.id, 0) + 1
        selected.append(candidate)
    strata_report = []
    for stratum in sampling.strata:
        achieved = per_stratum.get(stratum.id, 0)
        pool = [
            item
            for item in eligible
            if (
                stratum_of(item, sampling)
                or Stratum(id="", source_language="", caption_provenance="authored", requested=1)
            ).id
            == stratum.id
        ]
        strata_report.append(
            {
                "id": stratum.id,
                "source_language": stratum.source_language,
                "caption_provenance": stratum.caption_provenance,
                "requested": stratum.requested,
                "eligible_segments": len(pool),
                "eligible_videos": len({item.video_key for item in pool}),
                "selected": achieved,
                "missing": max(0, stratum.requested - achieved),
                "selected_videos": len(
                    {item.video_key for item in selected if stratum_of(item, sampling) == stratum}
                ),
            }
        )
    report = {
        "algorithm": sampling.algorithm,
        "seed": sampling.seed,
        "per_video_cap": sampling.per_video_cap,
        "minimum_gap_seconds": sampling.minimum_gap_seconds,
        "funnel": {
            **funnel,
            "rejected_per_video_cap": rejected_video_cap,
            "rejected_overlapping_neighbour": rejected_neighbours,
            "selected": len(selected),
        },
        "strata": strata_report,
        "requested_total": sampling.requested_total,
        "selected_total": len(selected),
        "complete": all(item["missing"] == 0 for item in strata_report),
    }
    selected.sort(key=lambda item: (item.source_language, item.video_key, item.clip_start))
    return selected, report


def quantiles(values: Sequence[float]) -> dict[str, float | None]:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
            "mean": None,
        }

    def percentile(fraction: float) -> float:
        if len(numbers) == 1:
            return numbers[0]
        position = fraction * (len(numbers) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return numbers[lower]
        return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)

    return {
        "count": len(numbers),
        "min": numbers[0],
        "p25": percentile(0.25),
        "median": statistics.median(numbers),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": numbers[-1],
        "mean": statistics.fmean(numbers),
    }


def group_summary(rows: Sequence[ResultRow], thresholds: Sequence[float]) -> dict[str, Any]:
    """Distributions with explicit denominators; means alone are never enough."""
    scored = [row for row in rows if row.score is not None and row.score.error_rate is not None]
    rates = [row.score.error_rate for row in scored if row.score is not None]
    reviewed = [row for row in rows if row.review is not None]
    confirmed = [
        row
        for row in reviewed
        if row.review is not None and row.review.caption_verdict == "incorrect"
    ]
    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row.status] = statuses.get(row.status, 0) + 1
    return {
        "segments": len(rows),
        "videos": len({row.video_key for row in rows}),
        "channels": len({row.channel for row in rows if row.channel}),
        "scored_segments": len(scored),
        "statuses": dict(sorted(statuses.items())),
        "disagreement": quantiles([value for value in rates if value is not None]),
        "at_or_above": {
            str(threshold): sum(1 for value in rates if value is not None and value >= threshold)
            for threshold in thresholds
        },
        "reviewed_segments": len(reviewed),
        "confirmed_caption_errors": len(confirmed),
        "review_verdicts": {
            verdict: sum(
                1
                for row in reviewed
                if row.review is not None and row.review.caption_verdict == verdict
            )
            for verdict in ("correct", "acceptable", "incorrect", "uncertain")
        },
        "reference_assessments": {
            assessment: sum(
                1
                for row in reviewed
                if row.review is not None and row.review.reference_assessment == assessment
            )
            for assessment in (
                "equivalent",
                "caption_better",
                "asr_better",
                "both_wrong",
                "uncertain",
            )
        },
    }


def aggregate(
    rows: Sequence[ResultRow],
    config: ExperimentConfig,
    *,
    thresholds: Sequence[float] = (0.1, 0.2, 0.3, 0.5),
) -> dict[str, Any]:
    """Report by predeclared source class, then by channel and acoustic breakdowns."""
    by_stratum = {
        stratum.id: group_summary([row for row in rows if row.stratum == stratum.id], thresholds)
        for stratum in config.sampling.strata
    }
    channels: dict[str, Any] = {}
    for channel in sorted({row.channel or "unknown" for row in rows}):
        channels[channel] = group_summary(
            [row for row in rows if (row.channel or "unknown") == channel], thresholds
        )
    acoustics: dict[str, Any] = {}
    for tag in config.acoustic_vocabulary:
        tagged = [row for row in rows if tag in row.acoustic_tags]
        if tagged:
            acoustics[tag] = group_summary(tagged, thresholds)
    features: dict[str, Any] = {}
    for row in rows:
        for feature in row.features:
            entry = features.setdefault(
                feature.get("feature", "unknown"),
                {"complete": 0, "unavailable": 0, "failed": 0, "versions": []},
            )
            entry[feature.get("status", "failed")] = entry.get(feature.get("status"), 0) + 1
            if feature.get("version") not in entry["versions"]:
                entry["versions"].append(feature.get("version"))
    return {
        "overall": group_summary(rows, thresholds),
        "by_source_class": by_stratum,
        "by_channel": channels,
        "by_acoustic_tag": acoustics,
        "features": features,
        "thresholds": list(thresholds),
        "metric_orientation": config.scoring.orientation,
        "interpretation": (
            "caption_asr_disagreement measures difference from a declared ASR reference; "
            "only manual adjudication makes a difference a confirmed caption error"
        ),
    }


def review_subset(rows: Sequence[ResultRow], review: ReviewSettings, seed: int) -> list[str]:
    """Predeclare which rows are reviewed before results are examined."""
    if review.subset == "all":
        return [row.segment_id for row in rows]
    selected: set[str] = {row.segment_id for row in rows if row.status != "complete"}
    for row in rows:
        if row.score is not None and row.score.error_rate is None:
            selected.add(row.segment_id)
    for stratum in sorted({row.stratum for row in rows}):
        for low, high in review.disagreement_bins:
            binned = [
                row
                for row in rows
                if row.stratum == stratum
                and row.score is not None
                and row.score.error_rate is not None
                and low <= row.score.error_rate < high
            ]
            binned.sort(key=lambda item: order_key(seed, item.segment_id))
            selected.update(row.segment_id for row in binned[: review.per_bin_per_stratum])
    return sorted(selected)
