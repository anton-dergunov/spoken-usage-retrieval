"""Pure logic for finding target-language speech in mixed-language audio.

Nothing here touches a model, a file, or the network. The runner feeds it VAD timestamps,
per-chunk detector outputs, and ground truth, and everything that decides or measures lives here
so it can be tested on hand-built inputs.

The method contract is deliberately narrow: a span decision may depend on the audio-derived
features of a chunk and on the catalogue's target language, and on nothing else. The "other"
language of a test clip is evaluation metadata and never reaches a decision function.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONFIG_VERSION = 1
RESULT_SCHEMA_VERSION = 1
LABEL_RUBRIC_VERSION = 1

HumanLabel = Literal["target", "other", "mixed", "no_speech", "unsure"]
HUMAN_LABELS: tuple[HumanLabel, ...] = ("target", "other", "mixed", "no_speech", "unsure")
Bucket = Literal["word", "phrase", "sentence", "long"]
BUCKETS: tuple[Bucket, ...] = ("word", "phrase", "sentence", "long")
Role = Literal["lead", "explain", "item", "inline_prefix", "inline_item", "inline_suffix"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------
# Configuration


class ClipSpec(Model):
    """One real test clip. ``evaluation_only`` is never passed to the method."""

    id: str
    file_pattern: str
    target_language: str
    evaluation_only: dict[str, Any] = Field(default_factory=dict)


class VadSettings(Model):
    threshold: float = Field(gt=0, lt=1)
    min_silence_ms: int = Field(ge=0)
    speech_pad_ms: int = Field(ge=0)
    min_speech_ms: int = Field(ge=0)


class ChunkSettings(Model):
    merge_gap_seconds: float = Field(ge=0)
    max_chunk_seconds: float = Field(gt=0)


class ReviewUnitSettings(Model):
    """Method-independent labelling units: fine VAD pieces, capped so a unit is rarely mixed."""

    merge_gap_seconds: float = Field(ge=0)
    max_unit_seconds: float = Field(gt=0)


class WhisperSettings(Model):
    model: str
    compute_type: str
    cpu_threads: int = Field(ge=1)
    beam_size: int = Field(ge=1)


class RefineSettings(Model):
    window_seconds: float = Field(gt=0)
    hop_seconds: float = Field(gt=0)
    min_chunk_seconds: float = Field(gt=0)
    switch_penalty: float = Field(ge=0)


class GateSettings(Model):
    """One operating point of the precision gate."""

    detector: str
    threshold: float = Field(ge=0, le=1)
    min_seconds: float = Field(ge=0)
    min_units: float = Field(ge=0)
    max_compression_ratio: float | None = None
    min_logprob_margin: float | None = None
    min_script_share: float | None = None
    text_lid_veto: bool = False


class SweepSettings(Model):
    detectors: list[str] = Field(min_length=1)
    thresholds: list[float] = Field(min_length=1)
    min_seconds: list[float] = Field(min_length=1)


class SelectionRule(Model):
    """How the operating point is chosen, fixed before any real clip is scored."""

    tuned_on: Literal["synthetic"] = "synthetic"
    min_span_precision: float = Field(gt=0, le=1)
    maximise: Literal["long_run_recall"] = "long_run_recall"


class SynthSettings(Model):
    seed: int
    blocks_per_clip: int = Field(ge=1)
    pairs: list[tuple[str, str]] = Field(min_length=1)
    voices: list[str] = Field(min_length=1)
    accented_targets: list[str] = Field(default_factory=list)


class MetricSettings(Model):
    long_run_seconds: float = Field(gt=0)
    run_join_gap_seconds: float = Field(ge=0)
    correct_span_min_target_fraction: float = Field(gt=0, le=1)
    hard_failure_max_target_fraction: float = Field(ge=0, lt=1)
    duration_buckets: list[float] = Field(default_factory=list)
    """Ascending bucket edges in seconds; ``[1, 2]`` means ``<1``, ``1-2``, ``>=2``."""
    useful_span_seconds: float = Field(default=0.0, ge=0)
    """Span length at and above which a span is long enough to be useful downstream."""


class ExperimentConfig(Model):
    config_version: Literal[1]
    run_id: str
    clips: list[ClipSpec] = Field(min_length=1)
    vad: VadSettings
    chunks: ChunkSettings
    review_units: ReviewUnitSettings
    whisper: WhisperSettings
    whisper_reference: WhisperSettings
    voxlingua_model: str
    closed_set_languages: list[str] = Field(min_length=2)
    refine: RefineSettings
    sweep: SweepSettings
    selection: SelectionRule
    fallback_gate: GateSettings
    synth: SynthSettings
    metrics: MetricSettings
    excluded: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Records


class DetectorOutput(Model):
    """One detector's raw evidence for one chunk or window."""

    detector: str
    p_target: float | None = None
    top: list[tuple[str, float]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ChunkRecord(Model):
    schema_version: Literal[1] = 1
    clip_id: str
    chunk_id: str
    start: float
    end: float
    detectors: list[DetectorOutput] = Field(default_factory=list)
    windows: list[WindowRecord] = Field(default_factory=list)


class WindowRecord(Model):
    start: float
    end: float
    detectors: list[DetectorOutput] = Field(default_factory=list)


class SpanRecord(Model):
    """An accepted target-language span with its final transcript."""

    schema_version: Literal[1] = 1
    clip_id: str
    method: str
    start: float
    end: float
    p_target: float
    reasons: list[str] = Field(default_factory=list)
    text: str | None = None
    words: list[dict[str, Any]] = Field(default_factory=list)
    avg_logprob: float | None = None
    compression_ratio: float | None = None


class TruthInterval(Model):
    start: float
    end: float
    language: str
    is_target: bool
    role: str | None = None
    bucket: str | None = None
    text: str | None = None


class LabelRecord(Model):
    clip_id: str
    unit_id: str
    start: float
    end: float
    label: HumanLabel | None = None
    note: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


ChunkRecord.model_rebuild()


# --------------------------------------------------------------------------------------------
# Intervals


@dataclass(frozen=True, slots=True)
class Interval:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def overlap_with(start: float, end: float, intervals: Iterable[tuple[float, float]]) -> float:
    return sum(overlap(start, end, s, e) for s, e in intervals)


def union_length(intervals: Iterable[tuple[float, float]]) -> float:
    total = 0.0
    current: list[float] | None = None
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if current is None or start > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current is not None:
        total += current[1] - current[0]
    return total


def covered(
    intervals: Iterable[tuple[float, float]], spans: Sequence[tuple[float, float]]
) -> float:
    """Seconds of ``intervals`` inside the union of ``spans``; overlapping spans count once."""
    return sum(
        union_length((max(s, start), min(e, end)) for s, e in spans if min(e, end) > max(s, start))
        for start, end in intervals
    )


def merge_intervals(
    stamps: Sequence[tuple[float, float]], merge_gap: float, max_seconds: float
) -> list[Interval]:
    """Join VAD pieces across short gaps without letting a chunk grow past ``max_seconds``.

    A single VAD piece longer than the cap is split into equal parts, since there is no pause
    inside it to split at.
    """
    pieces: list[Interval] = []
    for start, end in sorted(stamps):
        if end <= start:
            continue
        length = end - start
        parts = max(1, math.ceil(length / max_seconds - 1e-9))
        step = length / parts
        pieces.extend(Interval(start + i * step, start + (i + 1) * step) for i in range(parts))
    merged: list[Interval] = []
    for piece in pieces:
        if merged:
            last = merged[-1]
            if piece.start - last.end <= merge_gap and piece.end - last.start <= max_seconds:
                merged[-1] = Interval(last.start, piece.end)
                continue
        merged.append(piece)
    return merged


def sliding_windows(start: float, end: float, window: float, hop: float) -> list[Interval]:
    """Windows covering ``[start, end]``; the last one is aligned to the end, never truncated."""
    if end - start <= window:
        return [Interval(start, end)]
    windows: list[Interval] = []
    cursor = start
    while cursor + window < end - 1e-9:
        windows.append(Interval(cursor, cursor + window))
        cursor += hop
    windows.append(Interval(end - window, end))
    return windows


# --------------------------------------------------------------------------------------------
# Text


_CONTINUA = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿฀-๿]")
_WORD = re.compile(r"[^\W_]+")


def script_units(text: str) -> float:
    """Length in roughly word-sized units that is comparable across scripts.

    Han counts one per character, kana half, Thai one; every other run of letters is a word.
    """
    units = 0.0
    for character in text:
        if not _CONTINUA.match(character):
            continue
        name = unicodedata.name(character, "")
        units += 0.5 if ("HIRAGANA" in name or "KATAKANA" in name) else 1.0
    stripped = _CONTINUA.sub(" ", text)
    return units + len(_WORD.findall(stripped))


_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")

EXPECTED_SCRIPT = {
    "zh": "han",
    "ja": "japanese",
    "ko": "hangul",
    "ru": "cyrillic",
    "hi": "devanagari",
    "en": "latin",
    "es": "latin",
    "pt": "latin",
    "fr": "latin",
    "it": "latin",
    "de": "latin",
}


def script_share(text: str, language: str) -> float | None:
    """Share of letters in the script the target language is written in (Japanese: Han+kana)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    script = EXPECTED_SCRIPT.get(language)
    if script is None:
        return None
    pattern = {
        "han": _HAN,
        "hangul": _HANGUL,
        "cyrillic": _CYRILLIC,
        "devanagari": _DEVANAGARI,
        "latin": _LATIN,
    }.get(script)
    if script == "japanese":
        hits = sum(1 for c in letters if _HAN.match(c) or _KANA.match(c))
    else:
        assert pattern is not None
        hits = sum(1 for c in letters if pattern.match(c))
    return hits / len(letters)


# --------------------------------------------------------------------------------------------
# Language codes


_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-tw": "zh",
    "yue": "zh",
    "cmn": "zh",
    "pt-br": "pt",
    "pt-pt": "pt",
}


def normalise_language(code: str) -> str:
    """Reduce detector labels such as ``'es: Spanish'`` or ``'pt-BR'`` to a bare ISO 639-1 code."""
    head = code.split(":", 1)[0].strip().lower().replace("_", "-")
    return _ALIASES.get(head, head.split("-", 1)[0])


def probability_of(distribution: Iterable[tuple[str, float]], language: str) -> float:
    """Summed probability of every label that normalises to ``language``."""
    wanted = normalise_language(language)
    return float(sum(p for code, p in distribution if normalise_language(code) == wanted))


def pairwise_probability(
    top: Sequence[tuple[str, float]], p_target: float | None, language: str
) -> float | None:
    """Target against its strongest competitor: ``p(t) / (p(t) + max p(other))``.

    A binary verification score for a known target language. Every other language, including a
    close relative or the lesson language, can veto by being nearly as likely, which is the
    precision-leaning counterpart of :func:`closed_set_probability`.
    """
    if p_target is None:
        return None
    wanted = normalise_language(language)
    rivals = [p for code, p in top if normalise_language(code) != wanted]
    best = max(rivals, default=0.0)
    if p_target + best <= 0:
        return None
    return float(p_target / (p_target + best))


def closed_set_probability(
    distribution: Iterable[tuple[str, float]], language: str, languages: Sequence[str]
) -> float | None:
    """Probability of ``language`` renormalised over the languages the corpus can contain.

    A spoken-LID model spreads short Spanish over Catalan, Galician and Latin; none of those can be
    a catalogue language, so that mass is noise for this decision. English, Portuguese or Italian
    stay in the set, so the confusions that matter still compete.
    """
    allowed = {normalise_language(code) for code in languages}
    wanted = normalise_language(language)
    kept = [(normalise_language(code), p) for code, p in distribution]
    total = sum(p for code, p in kept if code in allowed)
    if total <= 0:
        return None
    return float(sum(p for code, p in kept if code == wanted) / total)


# --------------------------------------------------------------------------------------------
# Decisions


def viterbi_two_state(
    p_target: Sequence[float], switch_penalty: float, floor: float = 1e-4
) -> list[bool]:
    """Most likely target/other labelling of a window sequence under a per-switch penalty.

    Emission scores are log-probabilities; each change of state costs ``switch_penalty`` nats, so
    a single noisy window cannot flip a run but a sustained change of language can.
    """
    if not p_target:
        return []
    clamp = [min(1 - floor, max(floor, p)) for p in p_target]
    score = {True: math.log(clamp[0]), False: math.log(1 - clamp[0])}
    back: list[dict[bool, bool]] = []
    for p in clamp[1:]:
        emit = {True: math.log(p), False: math.log(1 - p)}
        step: dict[bool, bool] = {}
        new: dict[bool, float] = {}
        for state in (True, False):
            stay = score[state]
            switch = score[not state] - switch_penalty
            if stay >= switch:
                new[state], step[state] = stay + emit[state], state
            else:
                new[state], step[state] = switch + emit[state], not state
        score = new
        back.append(step)
    state = score[True] >= score[False]
    path = [state]
    for step in reversed(back):
        state = step[state]
        path.append(state)
    path.reverse()
    return path


def runs_from_windows(
    windows: Sequence[Interval], labels: Sequence[bool], p_target: Sequence[float]
) -> list[tuple[Interval, float]]:
    """Collapse consecutive target windows into spans with their mean window probability.

    Overlapping windows are split at their midpoints so neighbouring runs never overlap.
    """
    spans: list[tuple[Interval, float]] = []
    index = 0
    while index < len(windows):
        if not labels[index]:
            index += 1
            continue
        first = index
        while index + 1 < len(windows) and labels[index + 1]:
            index += 1
        last = index
        start = windows[first].start
        if first > 0:
            start = max(start, (windows[first - 1].end + windows[first].start) / 2)
        end = windows[last].end
        if last + 1 < len(windows):
            end = min(end, (windows[last].end + windows[last + 1].start) / 2)
        mean = sum(p_target[first : last + 1]) / (last - first + 1)
        spans.append((Interval(start, end), mean))
        index += 1
    return spans


@dataclass(frozen=True, slots=True)
class Candidate:
    """Everything the gate may look at. Built only from audio-derived features."""

    start: float
    end: float
    p_target: float | None
    units: float | None = None
    compression_ratio: float | None = None
    logprob_margin: float | None = None
    text_lid_is_target: bool | None = None
    script_share: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def gate(candidate: Candidate, settings: GateSettings) -> tuple[bool, list[str]]:
    """Accept a candidate span only when every configured precision check passes."""
    reasons: list[str] = []
    if candidate.p_target is None:
        reasons.append("no_detector_output")
    elif candidate.p_target < settings.threshold:
        reasons.append("below_threshold")
    if candidate.duration < settings.min_seconds:
        reasons.append("too_short")
    if settings.min_units and candidate.units is not None and candidate.units < settings.min_units:
        reasons.append("too_few_units")
    if (
        settings.max_compression_ratio is not None
        and candidate.compression_ratio is not None
        and candidate.compression_ratio > settings.max_compression_ratio
    ):
        reasons.append("repetitive_transcript")
    if (
        settings.min_logprob_margin is not None
        and candidate.logprob_margin is not None
        and candidate.logprob_margin < settings.min_logprob_margin
    ):
        reasons.append("likelihood_prefers_other")
    if (
        settings.min_script_share is not None
        and candidate.script_share is not None
        and candidate.script_share < settings.min_script_share
    ):
        reasons.append("transcript_in_wrong_script")
    if settings.text_lid_veto and candidate.text_lid_is_target is False:
        reasons.append("transcript_not_target")
    return not reasons, reasons


# --------------------------------------------------------------------------------------------
# Metrics against exact (synthetic) truth


def target_runs(truth: Sequence[TruthInterval], join_gap: float) -> list[Interval]:
    """Join target utterances separated by at most ``join_gap`` of silence into runs.

    Any other-language speech in between breaks the run.
    """
    runs: list[Interval] = []
    current: Interval | None = None
    for item in sorted(truth, key=lambda row: row.start):
        if not item.is_target:
            if current is not None:
                runs.append(current)
                current = None
            continue
        if current is not None and item.start - current.end <= join_gap:
            current = Interval(current.start, item.end)
        else:
            if current is not None:
                runs.append(current)
            current = Interval(item.start, item.end)
    if current is not None:
        runs.append(current)
    return runs


def score_against_truth(
    spans: Sequence[tuple[float, float]],
    truth: Sequence[TruthInterval],
    settings: MetricSettings,
) -> dict[str, Any]:
    """Time- and span-level precision and recall of accepted spans against exact intervals.

    Silence inside an accepted span is neither right nor wrong: only speech time enters the
    precision denominator.
    """
    target = [(row.start, row.end) for row in truth if row.is_target]
    other = [(row.start, row.end) for row in truth if not row.is_target]
    target_seconds = union_length(target)
    hit = covered(target, spans)
    miss = covered(other, spans)
    correct = 0
    hard = 0
    judged = 0
    for start, end in spans:
        on_target = overlap_with(start, end, target)
        on_other = overlap_with(start, end, other)
        speech = on_target + on_other
        if speech <= 0:
            # No judged speech under the span (unlabelled, silence, or only mixed/unsure units):
            # it can be neither right nor wrong, so it stays out of span precision.
            continue
        judged += 1
        fraction = on_target / speech
        if fraction >= settings.correct_span_min_target_fraction:
            correct += 1
        if fraction <= settings.hard_failure_max_target_fraction:
            hard += 1
    runs = target_runs(truth, settings.run_join_gap_seconds)
    long_runs = [run for run in runs if run.duration >= settings.long_run_seconds]

    def run_speech(run: Interval) -> list[tuple[float, float]]:
        return [
            (max(s, run.start), min(e, run.end))
            for s, e in target
            if overlap(s, e, run.start, run.end)
        ]

    long_total = sum(union_length(run_speech(run)) for run in long_runs)
    long_hit = sum(covered(run_speech(run), spans) for run in long_runs)
    covered_long = sum(
        1
        for run in long_runs
        if covered(run_speech(run), spans) >= 0.5 * union_length(run_speech(run))
    )
    return {
        "spans": len(spans),
        "judged_spans": judged,
        "correct_spans": correct,
        "hard_failures": hard,
        "span_precision": correct / judged if judged else None,
        "time_precision": hit / (hit + miss) if hit + miss > 0 else None,
        "time_recall": hit / target_seconds if target_seconds > 0 else None,
        "target_seconds": target_seconds,
        "other_seconds": union_length(other),
        "accepted_target_seconds": hit,
        "accepted_other_seconds": miss,
        "long_runs": len(long_runs),
        "long_runs_half_covered": covered_long,
        "long_run_target_seconds": long_total,
        "long_run_accepted_seconds": long_hit,
        "long_run_recall": long_hit / long_total if long_total > 0 else None,
    }


def pool_scores(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-clip scores as ratios of summed counts and seconds, never averaged ratios."""

    def total(key: str) -> float:
        return sum(result[key] for result in results)

    spans = int(total("spans"))
    judged = int(total("judged_spans"))
    hit, miss = total("accepted_target_seconds"), total("accepted_other_seconds")
    long_total = total("long_run_target_seconds")
    return {
        "clips": len(results),
        "spans": spans,
        "judged_spans": judged,
        "correct_spans": int(total("correct_spans")),
        "hard_failures": int(total("hard_failures")),
        "span_precision": total("correct_spans") / judged if judged else None,
        "time_precision": hit / (hit + miss) if hit + miss > 0 else None,
        "time_recall": hit / total("target_seconds") if total("target_seconds") > 0 else None,
        "target_seconds": total("target_seconds"),
        "accepted_target_seconds": hit,
        "accepted_other_seconds": miss,
        "long_runs": int(total("long_runs")),
        "long_runs_half_covered": int(total("long_runs_half_covered")),
        "long_run_recall": total("long_run_accepted_seconds") / long_total if long_total else None,
    }


# --------------------------------------------------------------------------------------------
# Metrics against human unit labels (real clips)


def labelled_truth(
    labels: Sequence[LabelRecord], *, mixed_as_other: bool = False
) -> list[TruthInterval]:
    """Human unit labels as truth intervals.

    ``target`` and ``other`` units are exact truth. ``mixed`` and ``unsure`` units are left out by
    default, because a unit records only *that* both languages occur in it, never *where* — the
    review units are raw VAD pieces, while the method cuts spans on a finer grid inside them.
    With ``mixed_as_other`` they count as other-language speech, which is the worst case for the
    method: it charges a span for the whole ambiguity of every mixed unit it touches, including
    units it deliberately cut into.
    """
    judged = [row for row in labels if row.label is not None]
    unclear: tuple[str, ...] = ("mixed", "unsure")
    truth = [
        TruthInterval(start=row.start, end=row.end, language="target", is_target=True)
        for row in judged
        if row.label == "target"
    ]
    truth += [
        TruthInterval(start=row.start, end=row.end, language="other", is_target=False)
        for row in judged
        if row.label == "other" or (mixed_as_other and row.label in unclear)
    ]
    return truth


def score_against_labels(
    spans: Sequence[tuple[float, float]],
    labels: Sequence[LabelRecord],
    settings: MetricSettings,
    *,
    mixed_as_other: bool = False,
) -> dict[str, Any]:
    """Precision and recall against per-unit human labels.

    ``target`` and ``other`` units are exact truth. A span's overlap with ``mixed`` or ``unsure``
    units cannot be judged, so it is reported separately instead of being counted either way;
    ``no_speech`` time is ignored like silence. ``mixed_as_other`` switches to the strict reading
    described in :func:`labelled_truth`.
    """
    judged = [row for row in labels if row.label is not None]
    unclear = [(row.start, row.end) for row in judged if row.label in ("mixed", "unsure")]
    truth = labelled_truth(labels, mixed_as_other=mixed_as_other)
    result = score_against_truth(spans, truth, settings)
    result["mixed_as_other"] = mixed_as_other
    result["unjudgeable_seconds_in_spans"] = sum(
        overlap_with(start, end, unclear) for start, end in spans
    )
    result["spans_touching_mixed_or_unsure"] = sum(
        1 for start, end in spans if overlap_with(start, end, unclear) > 0
    )
    result["labelled_units"] = len(judged)
    result["label_counts"] = {
        label: sum(1 for row in judged if row.label == label) for label in HUMAN_LABELS
    }
    return result


def bucket_edges(edges: Sequence[float]) -> list[tuple[str, float, float]]:
    """``[1, 2]`` becomes ``[("<1s", 0, 1), ("1-2s", 1, 2), (">=2s", 2, inf)]``."""
    if not edges:
        return [(">=0s", 0.0, math.inf)]
    ordered = sorted(edges)
    rows = [(f"<{_edge(ordered[0])}s", 0.0, ordered[0])]
    rows += [
        (f"{_edge(low)}-{_edge(high)}s", low, high)
        for low, high in zip(ordered, ordered[1:], strict=False)
    ]
    rows.append((f">={_edge(ordered[-1])}s", ordered[-1], math.inf))
    return rows


def _edge(value: float) -> str:
    return f"{value:g}"


def duration_breakdown(
    spans: Sequence[tuple[float, float]],
    labels: Sequence[LabelRecord],
    settings: MetricSettings,
    *,
    mixed_as_other: bool = False,
) -> dict[str, Any]:
    """Precision by accepted-span length and recall by target-run length.

    Short spans are of little use downstream — a learner needs a whole sentence — so the headline
    numbers should be read per length band, not pooled. ``by_span_duration`` buckets accepted spans
    and applies the usual 80 % criterion inside each bucket; ``by_run_duration`` buckets the truth's
    target runs and measures how much of each band's speech the spans recovered.
    """
    truth = labelled_truth(labels, mixed_as_other=mixed_as_other)
    target = [(row.start, row.end) for row in truth if row.is_target]
    other = [(row.start, row.end) for row in truth if not row.is_target]
    buckets = bucket_edges(settings.duration_buckets)

    by_span: dict[str, dict[str, Any]] = {
        name: {
            "spans": 0,
            "judged_spans": 0,
            "correct_spans": 0,
            "hard_failures": 0,
            "seconds": 0.0,
        }
        for name, _, _ in buckets
    }
    for start, end in spans:
        name = _bucket_for(end - start, buckets)
        row = by_span[name]
        row["spans"] += 1
        row["seconds"] += end - start
        on_target = overlap_with(start, end, target)
        on_other = overlap_with(start, end, other)
        speech = on_target + on_other
        if speech <= 0:
            continue
        row["judged_spans"] += 1
        fraction = on_target / speech
        if fraction >= settings.correct_span_min_target_fraction:
            row["correct_spans"] += 1
        if fraction <= settings.hard_failure_max_target_fraction:
            row["hard_failures"] += 1
    for row in by_span.values():
        judged = row["judged_spans"]
        row["span_precision"] = row["correct_spans"] / judged if judged else None

    by_run: dict[str, dict[str, Any]] = {
        name: {"runs": 0, "runs_half_covered": 0, "target_seconds": 0.0, "accepted_seconds": 0.0}
        for name, _, _ in buckets
    }
    for run in target_runs(truth, settings.run_join_gap_seconds):
        run_speech = [
            (max(s, run.start), min(e, run.end))
            for s, e in target
            if overlap(s, e, run.start, run.end)
        ]
        total = union_length(run_speech)
        hit = covered(run_speech, spans)
        row = by_run[_bucket_for(run.duration, buckets)]
        row["runs"] += 1
        row["runs_half_covered"] += 1 if total > 0 and hit >= 0.5 * total else 0
        row["target_seconds"] += total
        row["accepted_seconds"] += hit
    for row in by_run.values():
        total = row["target_seconds"]
        row["time_recall"] = row["accepted_seconds"] / total if total > 0 else None

    useful = [(start, end) for start, end in spans if end - start >= settings.useful_span_seconds]
    return {
        "buckets": [name for name, _, _ in buckets],
        "by_span_duration": by_span,
        "by_run_duration": by_run,
        "useful_span_seconds": settings.useful_span_seconds,
        "useful_spans_only": score_against_labels(
            useful, labels, settings, mixed_as_other=mixed_as_other
        ),
    }


def pool_duration_breakdowns(
    breakdowns: Sequence[dict[str, Any]], settings: MetricSettings
) -> dict[str, Any]:
    """Pool per-clip duration ladders by summing counts and seconds, never averaging ratios."""
    names = breakdowns[0]["buckets"] if breakdowns else [name for name, _, _ in bucket_edges([])]
    by_span = {
        name: _pool_bucket(
            [item["by_span_duration"][name] for item in breakdowns],
            ("spans", "judged_spans", "correct_spans", "hard_failures", "seconds"),
        )
        for name in names
    }
    for row in by_span.values():
        row["span_precision"] = (
            row["correct_spans"] / row["judged_spans"] if row["judged_spans"] else None
        )
    by_run = {
        name: _pool_bucket(
            [item["by_run_duration"][name] for item in breakdowns],
            ("runs", "runs_half_covered", "target_seconds", "accepted_seconds"),
        )
        for name in names
    }
    for row in by_run.values():
        row["time_recall"] = (
            row["accepted_seconds"] / row["target_seconds"] if row["target_seconds"] > 0 else None
        )
    return {
        "buckets": names,
        "by_span_duration": by_span,
        "by_run_duration": by_run,
        "useful_span_seconds": settings.useful_span_seconds,
        "useful_spans_only": pool_scores([item["useful_spans_only"] for item in breakdowns]),
    }


def _pool_bucket(rows: Sequence[dict[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    return {key: sum(row[key] for row in rows) for key in keys}


def pool_mixed_units(reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pooled = _pool_bucket(
        reports,
        (
            "units",
            "seconds",
            "untouched",
            "partially_cut",
            "fully_inside_a_span",
            "covered_seconds",
        ),
    )
    pooled["covered_fraction"] = (
        pooled["covered_seconds"] / pooled["seconds"] if pooled["seconds"] > 0 else None
    )
    return pooled


def _bucket_for(value: float, buckets: Sequence[tuple[str, float, float]]) -> str:
    for name, low, high in buckets:
        if low <= value < high:
            return name
    return buckets[-1][0]


def mixed_unit_report(
    spans: Sequence[tuple[float, float]], labels: Sequence[LabelRecord]
) -> dict[str, Any]:
    """How accepted spans fall across ``mixed``/``unsure`` units.

    A unit the method cut into is evidence that its span boundaries are finer than the labelling
    granularity, so strict scoring (``mixed_as_other``) charges it for time it never accepted.
    """
    unclear = [row for row in labels if row.label in ("mixed", "unsure")]
    untouched = partial = whole = 0
    seconds = covered_seconds = 0.0
    for row in unclear:
        duration = row.end - row.start
        hit = overlap_with(row.start, row.end, spans)
        seconds += duration
        covered_seconds += hit
        if hit <= 1e-6:
            untouched += 1
        elif hit >= duration - 0.05:
            whole += 1
        else:
            partial += 1
    return {
        "units": len(unclear),
        "seconds": seconds,
        "untouched": untouched,
        "partially_cut": partial,
        "fully_inside_a_span": whole,
        "covered_seconds": covered_seconds,
        "covered_fraction": covered_seconds / seconds if seconds > 0 else None,
    }


_VTT_CUE = re.compile(
    r"(\d+):(\d\d):(\d\d)\.(\d+) --> (\d+):(\d\d):(\d\d)\.(\d+)[^\n]*\n(.*?)(?:\n\s*\n|\Z)", re.S
)


def parse_vtt(text: str) -> list[tuple[float, float, list[str]]]:
    cues = []
    for match in _VTT_CUE.finditer(text):
        h1, m1, s1, f1, h2, m2, s2, f2, body = match.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + float("0." + f1)
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + float("0." + f2)
        lines = [re.sub(r"<[^>]+>", "", line).strip() for line in body.strip().splitlines()]
        cues.append((start, end, [line for line in lines if line]))
    return cues


def caption_proxy_labels(
    units: Sequence[dict[str, Any]],
    cues: Sequence[tuple[float, float, list[str]]],
    target: str,
    *,
    clip_id: str,
    uncaptioned_share: float = 0.2,
) -> list[LabelRecord]:
    """Evaluation-only proxy labels for a clip whose captions transcribe the *other* language.

    A cue whose first line is mostly in the target script is target speech; a cue with no
    target-script letters is other-language speech; anything else is mixed. A speech unit barely
    covered by any cue is assumed to be uncaptioned target speech. That assumption is exactly the
    clip-specific "caption complement" heuristic, which is why it may grade a method but never be
    one. Only defined for targets whose script differs from the captions' language.
    """
    typed: dict[HumanLabel, list[tuple[float, float]]] = {"target": [], "other": [], "mixed": []}
    for start, end, lines in cues:
        if not lines:
            continue
        first = script_share(lines[0], target) or 0.0
        anywhere = max((script_share(line, target) or 0.0) for line in lines)
        kind: HumanLabel = "target" if first >= 0.5 else ("other" if anywhere == 0.0 else "mixed")
        typed[kind].append((start, end))
    labels = []
    for unit in units:
        start, end = unit["start"], unit["end"]
        seconds = {kind: overlap_with(start, end, spans) for kind, spans in typed.items()}
        total = sum(seconds.values())
        if total < uncaptioned_share * (end - start):
            label: HumanLabel = "target"
        else:
            label = max(seconds, key=lambda name: seconds[name])
        labels.append(
            LabelRecord(
                clip_id=clip_id,
                unit_id=unit["unit_id"],
                start=start,
                end=end,
                label=label,
                reviewer="caption-proxy",
            )
        )
    return labels


# --------------------------------------------------------------------------------------------
# Operating-point selection


def select_operating_point(
    sweep: Sequence[dict[str, Any]], rule: SelectionRule
) -> dict[str, Any] | None:
    """Pick the sweep row that maximises long-run recall at the required span precision.

    Ties on recall prefer higher span precision, then the stricter point (higher threshold, then
    longer minimum), so the choice leans towards precision when recall is equal. The precision
    tie-break was added on 2026-09-15 after the first sweep showed two methods tying exactly on
    recall; see the experiment README.
    """
    eligible = [
        row
        for row in sweep
        if row.get("span_precision") is not None
        and row["span_precision"] >= rule.min_span_precision
        and row.get("long_run_recall") is not None
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            row["long_run_recall"],
            row["span_precision"],
            row["threshold"],
            row["min_seconds"],
        ),
    )


# --------------------------------------------------------------------------------------------
# Sampling and identity


def order_key(seed: int, identifier: str) -> str:
    return hashlib.sha256(f"{seed}\0{identifier}".encode()).hexdigest()


def canonical_checksum(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# --------------------------------------------------------------------------------------------
# Synthetic lesson composition


class Utterance(Model):
    """One TTS call in a synthetic clip. ``gap_before`` is silence inserted before it."""

    language: str
    text: str
    role: Role
    bucket: Bucket | None = None
    gap_before: float = Field(ge=0)
    voice_language: str | None = None


INLINE_GAP_SECONDS = 0.06
PAUSES = (0.25, 0.45, 0.8, 1.2)


def compose_lesson(
    bank: dict[str, Any], base: str, target: str, seed: int, blocks: int
) -> list[Utterance]:
    """Seeded lesson-style sequence alternating ``base`` and ``target``.

    Every clip contains each item bucket at least once, an inline switch inside a base-language
    sentence, and one long target run of two consecutive sentences, so both the easy and the
    unrecoverable cases are always present.
    """
    frames = bank["frames"][base]
    items = bank["items"][target]
    rng = random.Random(order_key(seed, f"{base}>{target}"))
    kinds = ["item:word", "item:phrase", "item:sentence", "item:long", "inline", "monologue"]
    while len(kinds) < blocks:
        kinds.append(rng.choice(["item:sentence", "item:phrase", "inline", "item:long"]))
    rng.shuffle(kinds)
    sequence: list[Utterance] = []

    def pause() -> float:
        return 0.0 if not sequence else rng.choice(PAUSES)

    for kind in kinds:
        if kind.startswith("item:"):
            bucket: Bucket = kind.split(":", 1)[1]  # type: ignore[assignment]
            sequence.append(
                Utterance(
                    language=base,
                    text=rng.choice(frames["lead"]),
                    role="lead",
                    gap_before=pause(),
                )
            )
            text = rng.choice(items[bucket])
            sequence.append(
                Utterance(
                    language=target, text=text, role="item", bucket=bucket, gap_before=pause()
                )
            )
            if bucket in ("word", "phrase") and rng.random() < 0.5:
                sequence.append(
                    Utterance(
                        language=target, text=text, role="item", bucket=bucket, gap_before=pause()
                    )
                )
            sequence.append(
                Utterance(
                    language=base,
                    text=rng.choice(frames["explain"]),
                    role="explain",
                    gap_before=pause(),
                )
            )
        elif kind == "inline":
            prefix, suffix = rng.choice(frames["inline"])
            bucket = rng.choice(["word", "phrase"])
            sequence.append(
                Utterance(language=base, text=prefix, role="inline_prefix", gap_before=pause())
            )
            sequence.append(
                Utterance(
                    language=target,
                    text=rng.choice(items[bucket]),
                    role="inline_item",
                    bucket=bucket,
                    gap_before=INLINE_GAP_SECONDS,
                )
            )
            sequence.append(
                Utterance(
                    language=base,
                    text=suffix,
                    role="inline_suffix",
                    gap_before=INLINE_GAP_SECONDS,
                )
            )
        else:
            first, second = rng.sample(items["sentence"] + items["long"], 2)
            sequence.append(
                Utterance(
                    language=base, text=rng.choice(frames["lead"]), role="lead", gap_before=pause()
                )
            )
            for text in (first, second):
                sequence.append(
                    Utterance(
                        language=target,
                        text=text,
                        role="item",
                        bucket="long" if text in items["long"] else "sentence",
                        gap_before=0.35 if text is second else pause(),
                    )
                )
    return sequence
