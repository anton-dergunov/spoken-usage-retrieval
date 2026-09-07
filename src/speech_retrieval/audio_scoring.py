"""Versioned text normalization and caption-to-reference disagreement scoring.

This is deliberately separate from :mod:`speech_retrieval.text`, whose retrieval normalizer
strips accents. Scoring must preserve diacritics, because an accent difference between a
caption and an ASR reference is exactly the kind of error the benchmark needs to expose.

The metric is oriented as ``reference = normalized ASR`` and ``hypothesis = normalized
caption``, so the denominator is reference tokens or characters. It measures disagreement
with a declared comparison reference, not confirmed caption error.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

SCORING_SCHEMA_VERSION = 1
APOSTROPHES = "'’‘ʼ´`"
ScoringUnit = Literal["word", "character"]
Operation = Literal["equal", "substitute", "delete", "insert"]


class ScoringError(ValueError):
    """Raised when a scoring request names an unknown normalization version."""


@dataclass(frozen=True, slots=True)
class ScoringNormalizer:
    """A named, frozen normalization contract; changing behavior requires a new version."""

    version: str
    unit: ScoringUnit
    fold_accents: bool = False
    keep_apostrophes: bool = True
    description: str = ""

    def normalize(self, text: str) -> str:
        value = unicodedata.normalize("NFC", str(text))
        for character in APOSTROPHES[1:]:
            value = value.replace(character, "'")
        value = value.casefold()
        if self.fold_accents:
            decomposed = unicodedata.normalize("NFD", value)
            value = unicodedata.normalize(
                "NFC", "".join(item for item in decomposed if not unicodedata.combining(item))
            )
        characters: list[str] = []
        for character in value:
            if character == "'" and self.keep_apostrophes and self.unit == "word":
                characters.append(character)
                continue
            category = unicodedata.category(character)
            if category[0] in {"P", "S", "C"}:
                characters.append(" ")
            elif category[0] == "Z":
                characters.append(" ")
            else:
                characters.append(character)
        collapsed = " ".join("".join(characters).split())
        return collapsed if self.unit == "word" else collapsed.replace(" ", "")

    def tokenize(self, text: str) -> list[str]:
        normalized = self.normalize(text)
        if self.unit == "character":
            return list(normalized)
        tokens = [token.strip("'") for token in normalized.split()]
        return [token for token in tokens if token]

    def payload(self) -> dict[str, Any]:
        return {
            "normalization_version": self.version,
            "scoring_schema_version": SCORING_SCHEMA_VERSION,
            "unit": self.unit,
            "fold_accents": self.fold_accents,
            "keep_apostrophes": self.keep_apostrophes,
            "preserves_diacritics": not self.fold_accents,
            "description": self.description,
        }


SCORING_NORMALIZERS: dict[str, ScoringNormalizer] = {
    "word-v1": ScoringNormalizer(
        version="word-v1",
        unit="word",
        description=(
            "NFC, case folding, apostrophe unification, punctuation to space, whitespace "
            "collapse; diacritics, digits, and filler words are preserved"
        ),
    ),
    "word-accent-folded-v1": ScoringNormalizer(
        version="word-accent-folded-v1",
        unit="word",
        fold_accents=True,
        description="word-v1 with combining marks removed; a named sensitivity analysis only",
    ),
    "character-v1": ScoringNormalizer(
        version="character-v1",
        unit="character",
        keep_apostrophes=False,
        description=(
            "NFC, case folding, punctuation and all whitespace removed, no script conversion; "
            "for languages scored by character such as Chinese and Japanese"
        ),
    ),
}

CHARACTER_SCORED_LANGUAGES = ("zh", "ja", "yue", "th", "lo", "my", "km", "bo")


def default_normalization(language: str) -> str:
    """Return the normalization version this project scores a language with."""
    primary = str(language).split("-", 1)[0].casefold()
    return "character-v1" if primary in CHARACTER_SCORED_LANGUAGES else "word-v1"


def get_normalizer(version: str) -> ScoringNormalizer:
    try:
        return SCORING_NORMALIZERS[version]
    except KeyError as error:
        raise ScoringError(f"unknown normalization version: {version}") from error


@dataclass(frozen=True, slots=True)
class AlignmentChunk:
    operation: Operation
    reference_start: int
    reference_end: int
    hypothesis_start: int
    hypothesis_end: int

    def payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "reference_start": self.reference_start,
            "reference_end": self.reference_end,
            "hypothesis_start": self.hypothesis_start,
            "hypothesis_end": self.hypothesis_end,
        }


@dataclass(frozen=True, slots=True)
class DisagreementScore:
    """Auditable per-item scoring output; the unbounded metric is never discarded."""

    metric: Literal["wer", "cer"]
    unit: ScoringUnit
    normalization_version: str
    reference_length: int
    hypothesis_length: int
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    error_rate: float | None
    agreement: float
    reference_text: str
    hypothesis_text: str
    normalized_reference: str
    normalized_hypothesis: str
    reference_tokens: tuple[str, ...] = ()
    hypothesis_tokens: tuple[str, ...] = ()
    alignment: tuple[AlignmentChunk, ...] = ()
    normalizer: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "unit": self.unit,
            "normalization_version": self.normalization_version,
            "reference_length": self.reference_length,
            "hypothesis_length": self.hypothesis_length,
            "hits": self.hits,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "error_rate": self.error_rate,
            "agreement": self.agreement,
            "reference_text": self.reference_text,
            "hypothesis_text": self.hypothesis_text,
            "normalized_reference": self.normalized_reference,
            "normalized_hypothesis": self.normalized_hypothesis,
            "reference_tokens": list(self.reference_tokens),
            "hypothesis_tokens": list(self.hypothesis_tokens),
            "alignment": [item.payload() for item in self.alignment],
            "normalizer": self.normalizer,
        }


@dataclass(frozen=True, slots=True)
class ErrorCounts:
    hits: int
    substitutions: int
    deletions: int
    insertions: int
    alignment: tuple[AlignmentChunk, ...]


def align_sequences(reference: list[str], hypothesis: list[str]) -> ErrorCounts:
    """Return exact hit/substitution/deletion/insertion counts and the merged alignment.

    ``reference`` is consumed by deletions and ``hypothesis`` by insertions, matching the
    conventional WER orientation.
    """
    rows = len(reference)
    columns = len(hypothesis)
    costs = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        costs[row][0] = row
    for column in range(1, columns + 1):
        costs[0][column] = column
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            if reference[row - 1] == hypothesis[column - 1]:
                costs[row][column] = costs[row - 1][column - 1]
                continue
            costs[row][column] = 1 + min(
                costs[row - 1][column - 1],
                costs[row - 1][column],
                costs[row][column - 1],
            )
    operations: list[Operation] = []
    row, column = rows, columns
    while row > 0 or column > 0:
        if (
            row > 0
            and column > 0
            and reference[row - 1] == hypothesis[column - 1]
            and costs[row][column] == costs[row - 1][column - 1]
        ):
            operations.append("equal")
            row -= 1
            column -= 1
        elif row > 0 and column > 0 and costs[row][column] == costs[row - 1][column - 1] + 1:
            operations.append("substitute")
            row -= 1
            column -= 1
        elif row > 0 and costs[row][column] == costs[row - 1][column] + 1:
            operations.append("delete")
            row -= 1
        else:
            operations.append("insert")
            column -= 1
    operations.reverse()
    chunks: list[AlignmentChunk] = []
    reference_index = 0
    hypothesis_index = 0
    for operation in operations:
        reference_step = 0 if operation == "insert" else 1
        hypothesis_step = 0 if operation == "delete" else 1
        if chunks and chunks[-1].operation == operation:
            previous = chunks[-1]
            chunks[-1] = AlignmentChunk(
                operation=operation,
                reference_start=previous.reference_start,
                reference_end=previous.reference_end + reference_step,
                hypothesis_start=previous.hypothesis_start,
                hypothesis_end=previous.hypothesis_end + hypothesis_step,
            )
        else:
            chunks.append(
                AlignmentChunk(
                    operation=operation,
                    reference_start=reference_index,
                    reference_end=reference_index + reference_step,
                    hypothesis_start=hypothesis_index,
                    hypothesis_end=hypothesis_index + hypothesis_step,
                )
            )
        reference_index += reference_step
        hypothesis_index += hypothesis_step
    return ErrorCounts(
        hits=sum(operation == "equal" for operation in operations),
        substitutions=sum(operation == "substitute" for operation in operations),
        deletions=sum(operation == "delete" for operation in operations),
        insertions=sum(operation == "insert" for operation in operations),
        alignment=tuple(chunks),
    )


def score_disagreement(
    *,
    reference_text: str,
    hypothesis_text: str,
    language: str | None = None,
    normalization_version: str | None = None,
) -> DisagreementScore:
    """Score a caption hypothesis against an ASR reference under a named normalizer.

    ``error_rate`` is None when the reference is empty and the hypothesis is not, because a
    rate has no denominator there. Bounded ``agreement`` exists for consumers that need
    ``[0, 1]`` and never replaces the unbounded diagnostic metric.
    """
    if normalization_version is None:
        if language is None:
            raise ScoringError("either language or normalization_version is required")
        normalization_version = default_normalization(language)
    normalizer = get_normalizer(normalization_version)
    reference = normalizer.tokenize(reference_text)
    hypothesis = normalizer.tokenize(hypothesis_text)
    counts = align_sequences(reference, hypothesis)
    errors = counts.substitutions + counts.deletions + counts.insertions
    if not reference:
        error_rate = 0.0 if not hypothesis else None
    else:
        error_rate = errors / len(reference)
    agreement = 0.0 if error_rate is None else max(0.0, 1.0 - error_rate)
    return DisagreementScore(
        metric="wer" if normalizer.unit == "word" else "cer",
        unit=normalizer.unit,
        normalization_version=normalizer.version,
        reference_length=len(reference),
        hypothesis_length=len(hypothesis),
        hits=counts.hits,
        substitutions=counts.substitutions,
        deletions=counts.deletions,
        insertions=counts.insertions,
        error_rate=error_rate,
        agreement=agreement,
        reference_text=str(reference_text),
        hypothesis_text=str(hypothesis_text),
        normalized_reference=normalizer.normalize(reference_text),
        normalized_hypothesis=normalizer.normalize(hypothesis_text),
        reference_tokens=tuple(reference),
        hypothesis_tokens=tuple(hypothesis),
        alignment=counts.alignment,
        normalizer=normalizer.payload(),
    )


def jiwer_reference_counts(
    reference_tokens: list[str], hypothesis_tokens: list[str], *, unit: ScoringUnit = "word"
) -> dict[str, int] | None:
    """Cross-check counts with JiWER when the optional scorer is installed.

    Already normalized sequences are passed through so JiWER's default transforms cannot
    drift under the experiment. Returns None when JiWER is not installed.
    """
    try:
        import jiwer
    except ImportError:
        return None
    if unit == "character":
        output = jiwer.process_characters(["".join(reference_tokens)], ["".join(hypothesis_tokens)])
    else:
        output = jiwer.process_words([" ".join(reference_tokens)], [" ".join(hypothesis_tokens)])
    return {
        "hits": int(output.hits),
        "substitutions": int(output.substitutions),
        "deletions": int(output.deletions),
        "insertions": int(output.insertions),
    }
