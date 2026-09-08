"""Configuration, result types, sampling, and scoring for the forced-alignment experiment.

The measurement problem this file exists to handle honestly: there is no ground truth for
word times. What there is:

* YouTube's automatic-caption word *start* times, parsed from json3 ``tOffsetMs``. Real data,
  but it is another aligner's output, not truth. Its *end* times are not usable at all --
  ``captions.automatic_units`` estimates them as ``min(end, start + 0.4)`` capped by the next
  start -- so only starts are ever scored here.
* The cue-level timing the product uses today, which is the bar any aligner has to beat.
* A human listening to the audio, which is the arbiter when the two disagree.

Everything below reports *agreement* rather than accuracy, keeps signed error separate from
absolute error so a systematic offset in the reference stays visible, and bootstraps over
videos rather than words because a drifting alignment misses every word in a segment at once.
"""

from __future__ import annotations

import difflib
import math
import random
import statistics
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from speech_retrieval.text import normalize_token, tokens_with_spans

RESULT_SCHEMA_VERSION = 1
RUBRIC_VERSION = "alignment-review-v1"

SourceClass = Literal["authored", "automatic"]
SystemName = str

#: The systems compared. The two baselines are what the product does without a model, and are
#: the reason the experiment can answer "is alignment worth it" rather than only "which model".
CUE_START = "cue_start"
CUE_INTERPOLATED = "cue_interpolated"
BASELINES = (CUE_START, CUE_INTERPOLATED)

#: Human ratings, worst to best order matters for the agreement statistics.
SYNC_RATINGS: tuple[tuple[str, str, str], ...] = (
    (
        "in_sync",
        "In sync",
        "The highlight lands on each word as you hear it. You would not notice a problem.",
    ),
    (
        "slightly_off",
        "Slightly off",
        "Usable, but the highlight consistently leads or trails the voice, or drifts by a "
        "word here and there.",
    ),
    (
        "broken",
        "Broken",
        "The highlight does not correspond to the speech: it jumps, stalls, or is on the "
        "wrong words entirely.",
    ),
)
RATING_ORDER = {"in_sync": 0, "slightly_off": 1, "broken": 2}

SYNC_TAGS: dict[str, str] = {
    "drifts_late": "The highlight is consistently behind the voice.",
    "drifts_early": "The highlight is consistently ahead of the voice.",
    "jumps": "The highlight moves in sudden leaps rather than word by word.",
    "stalls": "The highlight stops while speech continues, then catches up.",
    "unclear_audio": "The speech itself is hard to follow, so sync is hard to judge.",
}


# --- Configuration ------------------------------------------------------------------------


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str
    profile: Literal["mms", "permissive"]


class Sampling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Target segments per (language x source class) cell. 100 gives roughly +-8 points on a
    #: proportion once clustered by video; 30 gives +-14, which cannot separate two systems.
    target_per_cell: int = 100
    #: Below this a cell is reported as indicative only.
    minimum_per_cell: int = 50
    #: Cap the measurement stages. preflight measures throughput and reduces the per-cell
    #: target to fit rather than letting the run overrun.
    time_budget_seconds: float = 600.0
    min_tokens: int = 4
    max_clip_seconds: float = 20.0
    review_clips: int = 20
    #: Clips shown twice, in a different A/B/C order, to measure reviewer self-consistency.
    #: Without this, any human-versus-engine agreement number has no ceiling to compare to.
    review_repeats: int = 3
    seed: int = 20260908


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    languages: list[str] = Field(default_factory=lambda: ["es", "en", "ru"])
    models: list[Model]
    sampling: Sampling = Field(default_factory=Sampling)
    #: Agreement thresholds reported in the results table, in seconds.
    tolerances: list[float] = Field(default_factory=lambda: [0.1, 0.2, 0.3])
    notes: str = ""


# --- Results ------------------------------------------------------------------------------


class WordComparison(BaseModel):
    """One matched word pair: the aligner's start against the reference start."""

    model_config = ConfigDict(extra="forbid")

    text: str
    aligned_start: float
    reference_start: float

    @property
    def delta(self) -> float:
        """Signed error. Positive means the aligner is late relative to the reference."""
        return self.aligned_start - self.reference_start


class TimedGroup(BaseModel):
    """One timed word, carrying its character range in the source text.

    The review page highlights by character range rather than by word position. Position-based
    matching silently breaks whenever a system times a different number of words than the text
    displays, which is exactly the defect that invalidated the first review pass.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    char_start: int
    char_end: int
    start: float
    end: float | None = None


class SystemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: SystemName
    status: str
    coverage: float = 0.0
    mean_confidence: float | None = None
    seconds: float = 0.0
    #: What this system actually produced, for rendering. Distinct from ``comparisons``, which is
    #: only the subset that could be matched against the reference for scoring.
    groups: list[TimedGroup] = Field(default_factory=list)
    comparisons: list[WordComparison] = Field(default_factory=list)
    reason: str | None = None


class ResultRow(BaseModel):
    """One sampled segment, with every system's attempt at it."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    segment_id: str
    video_key: str
    channel: str | None = None
    language: str
    source_class: SourceClass
    text: str
    clip: str | None = None
    clip_start: float = 0.0
    clip_end: float = 0.0
    reference_words: int = 0
    systems: list[SystemResult] = Field(default_factory=list)
    stage: str = "sampled"
    error: str | None = None

    def system(self, name: str) -> SystemResult | None:
        return next((item for item in self.systems if item.system == name), None)


class ReviewItem(BaseModel):
    """One row of the blind karaoke worksheet."""

    model_config = ConfigDict(extra="forbid")

    review_id: str
    segment_id: str
    language: str
    source_class: SourceClass
    text: str
    clip: str | None
    duration: float
    #: Which system each of the visible labels A/B/C actually is. Withheld from the page until
    #: the row is submitted, so the reviewer cannot be anchored by the model name.
    assignment: dict[str, str]
    #: A repeat of an earlier row, shown with a different permutation.
    repeat_of: str | None = None
    ratings: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, list[str]] = Field(default_factory=dict)
    note: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


# --- Reference extraction --------------------------------------------------------------------


def reference_starts(units: Sequence[Any], clip_start: float) -> list[tuple[str, float]]:
    """Word starts from YouTube's automatic track, relative to the clip.

    Only starts are returned. The end times in ``TimedUnit`` for automatic captions are
    estimated by the parser, not supplied by YouTube, so scoring them would measure this
    repository's own heuristic rather than the provider's timing.
    """
    return [
        (unit.text, round(unit.start - clip_start, 4))
        for unit in units
        if unit.text and unit.text.strip()
    ]


def match_words(
    aligned: Sequence[tuple[str, float]],
    reference: Sequence[tuple[str, float]],
) -> list[WordComparison]:
    """Pair aligned words with reference words by normalized-token sequence alignment.

    Only equal runs contribute, so a word the ASR transcribed differently is dropped rather
    than scored as a timing error. That is what lets an authored caption be compared against
    an automatic track without the text difference contaminating the timing measurement.
    """
    left = [normalize_token(text) for text, _ in aligned]
    right = [normalize_token(text) for text, _ in reference]
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    pairs: list[WordComparison] = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            aligned_text, aligned_start = aligned[block.a + offset]
            _, reference_start = reference[block.b + offset]
            pairs.append(
                WordComparison(
                    text=aligned_text,
                    aligned_start=aligned_start,
                    reference_start=reference_start,
                )
            )
    return pairs


# --- Baselines ---------------------------------------------------------------------------------


def cue_start_groups(text: str, cue_start: float) -> list[TimedGroup]:
    """Every word gets the cue's start: what a player does with one timing unit."""
    return [
        TimedGroup(text=token.text, char_start=token.start, char_end=token.end, start=cue_start)
        for token in tokens_with_spans(text)
    ]


def cue_interpolated_groups(text: str, cue_start: float, cue_end: float) -> list[TimedGroup]:
    """Spread words across the cue in proportion to their character position.

    This is the strongest timing achievable with no acoustic model at all, and therefore the
    honest bar. Beating cue-start is easy; beating this is the question.
    """
    tokens = tokens_with_spans(text)
    if not tokens or cue_end <= cue_start:
        return cue_start_groups(text, cue_start)
    span = len(text) or 1
    duration = cue_end - cue_start
    return [
        TimedGroup(
            text=token.text,
            char_start=token.start,
            char_end=token.end,
            start=round(cue_start + duration * (token.start / span), 4),
        )
        for token in tokens
    ]


def group_starts(groups: Sequence[TimedGroup]) -> list[tuple[str, float]]:
    """The ``(text, start)`` pairs :func:`match_words` scores against the reference."""
    return [(group.text, group.start) for group in groups]


# --- Statistics -----------------------------------------------------------------------------------


def quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def bootstrap_interval(
    clusters: Sequence[Sequence[float]],
    statistic: str = "median_abs",
    *,
    tolerance: float = 0.2,
    resamples: int = 1000,
    seed: int = 12345,
) -> tuple[float, float]:
    """A 95% interval resampled over *videos*, not words.

    Words inside one segment fail together when an alignment drifts, so resampling words
    would report an interval several times tighter than the evidence supports.
    """
    pool = [cluster for cluster in clusters if cluster]
    if len(pool) < 2:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(resamples):
        drawn = [pool[rng.randrange(len(pool))] for _ in range(len(pool))]
        flat = [value for cluster in drawn for value in cluster]
        if not flat:
            continue
        estimates.append(_statistic(flat, statistic, tolerance))
    if not estimates:
        return (math.nan, math.nan)
    return (round(quantile(estimates, 0.025), 4), round(quantile(estimates, 0.975), 4))


def _statistic(values: Sequence[float], name: str, tolerance: float) -> float:
    if name == "median_abs":
        return quantile([abs(value) for value in values], 0.5)
    if name == "within":
        return sum(1 for value in values if abs(value) <= tolerance) / len(values)
    raise ValueError(f"unknown statistic {name!r}")


class SystemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: SystemName
    language: str
    source_class: SourceClass
    segments: int
    videos: int
    words: int
    median_abs: float
    p90_abs: float
    median_signed: float
    within: dict[str, float]
    median_abs_ci: tuple[float, float]
    within_200ms_ci: tuple[float, float]
    coverage: float
    failures: int
    seconds_per_audio_second: float
    indicative_only: bool


def summarize(
    rows: Iterable[ResultRow],
    system: str,
    *,
    tolerances: Sequence[float] = (0.1, 0.2, 0.3),
    minimum_per_cell: int = 50,
) -> SystemSummary | None:
    """Aggregate one system within one (language, source class) cell."""
    rows = [row for row in rows]
    if not rows:
        return None
    by_video: dict[str, list[float]] = {}
    deltas: list[float] = []
    coverages: list[float] = []
    seconds = 0.0
    audio_seconds = 0.0
    failures = 0
    scored_segments = 0
    for row in rows:
        result = row.system(system)
        if result is None:
            continue
        seconds += result.seconds
        audio_seconds += max(row.clip_end - row.clip_start, 0.0)
        if result.status in ("failed", "unavailable") or not result.comparisons:
            failures += 1
            continue
        scored_segments += 1
        coverages.append(result.coverage)
        row_deltas = [item.delta for item in result.comparisons]
        deltas.extend(row_deltas)
        by_video.setdefault(row.video_key, []).extend(row_deltas)
    if not deltas:
        return None
    absolute = [abs(value) for value in deltas]
    clusters = list(by_video.values())
    return SystemSummary(
        system=system,
        language=rows[0].language,
        source_class=rows[0].source_class,
        segments=scored_segments,
        videos=len(by_video),
        words=len(deltas),
        median_abs=round(quantile(absolute, 0.5), 4),
        p90_abs=round(quantile(absolute, 0.9), 4),
        median_signed=round(quantile(deltas, 0.5), 4),
        within={
            f"{int(tolerance * 1000)}ms": round(
                sum(1 for value in absolute if value <= tolerance) / len(absolute), 4
            )
            for tolerance in tolerances
        },
        median_abs_ci=bootstrap_interval(clusters, "median_abs"),
        within_200ms_ci=bootstrap_interval(clusters, "within", tolerance=0.2),
        coverage=round(statistics.fmean(coverages), 4) if coverages else 0.0,
        failures=failures,
        seconds_per_audio_second=round(seconds / audio_seconds, 4) if audio_seconds else 0.0,
        indicative_only=scored_segments < minimum_per_cell,
    )


def engine_agreement(rows: Iterable[ResultRow], left: str, right: str) -> dict[str, Any]:
    """How closely two engines agree with each other on the same words.

    This is the noise floor for every other number in the report: if two independently
    trained models agree to within X, no comparison against a third-party reference can
    resolve differences much smaller than X.
    """
    deltas: list[float] = []
    for row in rows:
        first, second = row.system(left), row.system(right)
        if not first or not second or not first.comparisons or not second.comparisons:
            continue
        starts = {item.text: item.aligned_start for item in second.comparisons}
        for item in first.comparisons:
            other = starts.get(item.text)
            if other is not None:
                deltas.append(item.aligned_start - other)
    if not deltas:
        return {"pairs": 0}
    absolute = [abs(value) for value in deltas]
    return {
        "pairs": len(deltas),
        "median_abs": round(quantile(absolute, 0.5), 4),
        "p90_abs": round(quantile(absolute, 0.9), 4),
        "median_signed": round(quantile(deltas, 0.5), 4),
        "within_200ms": round(sum(1 for value in absolute if value <= 0.2) / len(absolute), 4),
    }


def kendall_tau(left: Sequence[float], right: Sequence[float]) -> float:
    """Rank correlation over paired observations, with ties handled the tau-b way."""
    if len(left) != len(right) or len(left) < 2:
        return math.nan
    concordant = discordant = tied_left = tied_right = 0
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            a = left[i] - left[j]
            b = right[i] - right[j]
            product = a * b
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
            else:
                if a == 0:
                    tied_left += 1
                if b == 0:
                    tied_right += 1
    denominator = math.sqrt(
        (concordant + discordant + tied_left) * (concordant + discordant + tied_right)
    )
    return round((concordant - discordant) / denominator, 4) if denominator else math.nan


def review_agreement(items: Sequence[ReviewItem], rows: Sequence[ResultRow]) -> dict[str, Any]:
    """Compare human ratings against measured error, and the reviewer against themselves.

    The decisive comparison is between this and :func:`engine_agreement`. If the engines agree
    with each other far more tightly than the human agrees with the measured ordering, the
    millisecond thresholds are measuring something the ear does not care about, and the
    run/skip rule has to be built on the perceptual categories instead.
    """
    by_segment = {row.segment_id: row for row in rows}
    judged = [item for item in items if item.ratings and item.repeat_of is None]

    ratings: list[float] = []
    errors: list[float] = []
    hits = 0
    comparable = 0
    for item in judged:
        row = by_segment.get(item.segment_id)
        if row is None:
            continue
        measured: dict[str, float] = {}
        for label, system in item.assignment.items():
            result = row.system(system)
            if result and result.comparisons:
                measured[label] = quantile([abs(pair.delta) for pair in result.comparisons], 0.5)
        shared = [label for label in item.ratings if label in measured]
        if len(shared) < 2:
            continue
        comparable += 1
        for label in shared:
            ratings.append(RATING_ORDER.get(item.ratings[label], 1))
            errors.append(measured[label])
        best_rated = min(shared, key=lambda label: RATING_ORDER.get(item.ratings[label], 1))
        best_measured = min(shared, key=lambda label: measured[label])
        hits += int(
            RATING_ORDER.get(item.ratings[best_rated], 1)
            == RATING_ORDER.get(item.ratings[best_measured], 1)
        )

    repeats = [item for item in items if item.repeat_of and item.ratings]
    original = {item.review_id: item for item in items}
    consistent = 0
    for item in repeats:
        first = original.get(item.repeat_of or "")
        if first is None or not first.ratings:
            continue
        by_system_first = {
            first.assignment[label]: rating for label, rating in first.ratings.items()
        }
        by_system_again = {item.assignment[label]: rating for label, rating in item.ratings.items()}
        shared_systems = set(by_system_first) & set(by_system_again)
        if shared_systems and all(
            by_system_first[key] == by_system_again[key] for key in shared_systems
        ):
            consistent += 1

    return {
        "clips_judged": len(judged),
        "clips_comparable": comparable,
        "rating_vs_error_tau": kendall_tau(ratings, errors),
        "top_choice_hit_rate": round(hits / comparable, 4) if comparable else math.nan,
        "repeats": len(repeats),
        "self_consistent": consistent,
        "self_consistency_rate": round(consistent / len(repeats), 4) if repeats else math.nan,
    }


# --- Sampling -------------------------------------------------------------------------------------


def stable_order(values: Iterable[Any], key: str, seed: int) -> list[Any]:
    """Deterministic shuffle by hashed key, so a re-run picks the same rows."""
    import hashlib

    def order(item: Any) -> str:
        raw = f"{seed}:{getattr(item, key, None) or item[key]}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return sorted(values, key=order)


def choose_review_clips(
    rows: Sequence[ResultRow],
    systems: Sequence[str],
    sampling: Sampling,
) -> list[ReviewItem]:
    """Pick the review subset and assign blind labels.

    Stratified across language, source class, and predicted confidence so the review can check
    whether the confidence signal means anything, rather than sampling only easy rows.
    """
    rng = random.Random(sampling.seed)
    usable = [
        row
        for row in rows
        if row.clip and all((result := row.system(name)) and result.comparisons for name in systems)
    ]
    if not usable:
        return []

    def confidence_of(row: ResultRow) -> float:
        values = [
            result.mean_confidence
            for name in systems
            if (result := row.system(name)) and result.mean_confidence is not None
        ]
        return statistics.fmean(values) if values else 0.0

    ordered = stable_order(usable, "segment_id", sampling.seed)
    buckets: dict[tuple[str, str, str], list[ResultRow]] = {}
    midpoint = statistics.median([confidence_of(row) for row in ordered]) if ordered else 0.0
    for row in ordered:
        band = "high" if confidence_of(row) >= midpoint else "low"
        buckets.setdefault((row.language, row.source_class, band), []).append(row)

    chosen: list[ResultRow] = []
    while len(chosen) < sampling.review_clips and any(buckets.values()):
        for key in sorted(buckets):
            if len(chosen) >= sampling.review_clips:
                break
            if buckets[key]:
                chosen.append(buckets[key].pop(0))

    items: list[ReviewItem] = []
    for index, row in enumerate(chosen):
        items.append(_review_item(f"r{index + 1:02d}", row, systems, rng))
    for index, row in enumerate(chosen[: sampling.review_repeats]):
        repeat = _review_item(f"x{index + 1:02d}", row, systems, rng)
        items.append(repeat.model_copy(update={"repeat_of": f"r{index + 1:02d}"}))
    # Interleave the repeats rather than leaving them last, so they are not obviously repeats.
    rng.shuffle(items)
    return items


def _review_item(
    review_id: str, row: ResultRow, systems: Sequence[str], rng: random.Random
) -> ReviewItem:
    labels = [chr(ord("A") + index) for index in range(len(systems))]
    shuffled = list(systems)
    rng.shuffle(shuffled)
    return ReviewItem(
        review_id=review_id,
        segment_id=row.segment_id,
        language=row.language,
        source_class=row.source_class,
        text=row.text,
        clip=row.clip,
        duration=round(max(row.clip_end - row.clip_start, 0.0), 3),
        assignment=dict(zip(labels, shuffled, strict=True)),
    )
