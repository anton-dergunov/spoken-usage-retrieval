"""Types and the protocol for timing known source text against prepared audio.

This module never imports an acoustic model or any optional dependency, so the rest of
the application can describe, store, and serve alignment without the ``alignment`` extra
installed. The model-backed implementation lives in :mod:`speech_retrieval.alignment_ctc`.

Aligning known text to audio is a CTC forced-alignment task, not a transcription task. The
source text is the input, never the output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

ALIGNMENT_SCHEMA_VERSION = 1

AlignmentStatus = Literal["complete", "partial", "unavailable", "failed"]
MatchStatus = Literal["matched", "unmatched", "punctuation"]

#: Reasons an alignment could not be produced. Recorded verbatim in provenance so a stored
#: failure explains itself without needing the code that produced it.
UnavailableReason = Literal[
    "dependency_missing",
    "model_unavailable",
    "language_unsupported",
    "audio_missing",
    "non_commercial_refused",
    "text_empty",
]


@dataclass(frozen=True, slots=True)
class AlignedGroup:
    """One contiguous run of source characters, with audio times when they were matched.

    ``char_start``/``char_end`` index the *original* source string, so groups reconstruct it
    exactly. ``start``/``end`` are ``None`` for anything the model did not match: unmatched
    text keeps its place in the sequence rather than being dropped or given a guessed time.
    """

    text: str
    char_start: int
    char_end: int
    start: float | None
    end: float | None
    match_status: MatchStatus
    confidence: float | None = None

    @property
    def timed(self) -> bool:
        return self.start is not None and self.end is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_timed_text(self) -> dict[str, Any] | None:
        """Return the player's ``TimedText`` shape, or ``None`` when this group has no time.

        The React player consumes ``{text, start, end, char_start, char_end}`` as
        ``sourceTiming``. Untimed groups are omitted from that view rather than being given a
        fabricated span; the player already renders the gaps as plain text.
        """
        if self.start is None or self.end is None:
            return None
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass(frozen=True, slots=True)
class AlignmentProvenance:
    """Identity of everything that could change an alignment result.

    ``model_license`` is mandatory. A downstream host must be able to tell, from stored data
    alone, whether an alignment came from a non-commercial model, without re-deriving it from
    the model name.
    """

    aligner: str
    model_id: str
    model_license: str
    device: str
    settings_hash: str
    model_revision: str | None = None
    package_versions: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Versioned alignment output. Always safe to serialise and store."""

    status: AlignmentStatus
    groups: tuple[AlignedGroup, ...] = ()
    coverage: float = 0.0
    provenance: AlignmentProvenance | None = None
    reason: str | None = None
    schema_version: int = ALIGNMENT_SCHEMA_VERSION

    @property
    def usable(self) -> bool:
        """Whether this result carries timing a caller can render."""
        return self.status in ("complete", "partial") and any(group.timed for group in self.groups)

    def timed_text(self) -> list[dict[str, Any]]:
        """The ``sourceTiming`` payload for the player, in source order."""
        rendered = (group.as_timed_text() for group in self.groups)
        return [item for item in rendered if item is not None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "groups": [group.as_dict() for group in self.groups],
            "coverage": self.coverage,
            "provenance": self.provenance.as_dict() if self.provenance else None,
            "reason": self.reason,
            "schema_version": self.schema_version,
        }


@runtime_checkable
class Aligner(Protocol):
    """Times known source text against a prepared audio clip."""

    def align(self, text: str, source_language: str, audio_clip: Any) -> AlignmentResult:
        """Return timed source character groups for ``text`` spoken in ``audio_clip``.

        ``audio_clip`` is a :class:`speech_retrieval.audio.PreparedClip`. Implementations must
        return an unavailable or failed result rather than raising, so a missing model or an
        unsupported language never breaks playback.
        """
        ...


def unavailable_result(
    reason: UnavailableReason | str,
    *,
    provenance: AlignmentProvenance | None = None,
) -> AlignmentResult:
    """The dependency-free fallback: no timing, an explicit reason, nothing fabricated."""
    return AlignmentResult(status="unavailable", reason=str(reason), provenance=provenance)


def failed_result(reason: str, *, provenance: AlignmentProvenance | None = None) -> AlignmentResult:
    """A model ran but its output did not meet the documented checks."""
    return AlignmentResult(status="failed", reason=reason, provenance=provenance)


def settings_hash(settings: dict[str, Any]) -> str:
    """A stable digest of alignment settings, for cache keys and provenance."""
    encoded = json.dumps(settings, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def coverage_of(groups: Sequence[AlignedGroup], text: str) -> float:
    """Fraction of *matchable* source characters that received a time.

    Only alphanumeric characters count. Punctuation is never spoken and so can never be
    timed; counting it would cap coverage below 1.0 for any ordinary sentence and make the
    complete/partial distinction meaningless.
    """
    total = sum(1 for char in text if char.isalnum())
    if not total:
        return 0.0
    matched = sum(1 for group in groups if group.timed for char in group.text if char.isalnum())
    return round(matched / total, 4)


class AlignmentGroupError(ValueError):
    """Raised when a produced group sequence does not describe the source text."""


def validate_groups(groups: Sequence[AlignedGroup], text: str) -> None:
    """Assert the invariants every consumer relies on.

    Groups must be ordered, non-overlapping, in bounds, and must reconstruct ``text`` exactly.
    This is what lets a caller trust character offsets for highlighting, and it is checked in
    production rather than only in tests because a silent off-by-one would corrupt the cache.
    """
    cursor = 0
    for index, group in enumerate(groups):
        if group.char_start != cursor:
            raise AlignmentGroupError(
                f"group {index} starts at {group.char_start}, expected {cursor}"
            )
        if group.char_end < group.char_start:
            raise AlignmentGroupError(f"group {index} ends before it starts")
        if group.char_end > len(text):
            raise AlignmentGroupError(
                f"group {index} ends at {group.char_end}, beyond {len(text)} characters"
            )
        if group.text != text[group.char_start : group.char_end]:
            raise AlignmentGroupError(f"group {index} text does not match its character range")
        if group.timed:
            assert group.start is not None and group.end is not None  # narrowed by .timed
            if group.end < group.start:
                raise AlignmentGroupError(f"group {index} ends before it starts in time")
        elif group.start is not None or group.end is not None:
            raise AlignmentGroupError(
                f"group {index} is partially timed; a group is either timed or it is not"
            )
        cursor = group.char_end
    if cursor != len(text):
        raise AlignmentGroupError(f"groups cover {cursor} of {len(text)} characters")


# --- Model registry -------------------------------------------------------------------
#
# Kept here, not in the model-backed module, so settings validation and ``doctor`` can
# report which model would be used and under which license without importing torch.


@dataclass(frozen=True, slots=True)
class AlignmentModel:
    """A CTC checkpoint usable for forced alignment, and the terms it comes under."""

    model_id: str
    license: str
    #: Romanize source text before matching it to the vocabulary. Required for MMS, whose
    #: vocabulary is romanized Latin for every one of its languages; wrong for native-script
    #: checkpoints, whose vocabulary already contains the language's own characters.
    romanize: bool
    #: Languages this checkpoint covers. Empty means "any language" (MMS).
    languages: tuple[str, ...] = ()

    @property
    def commercial_use_allowed(self) -> bool:
        return "nc" not in self.license.lower().split("-")

    def supports(self, language: str) -> bool:
        return not self.languages or language in self.languages


#: The default profile. One model for every language, best available quality, and
#: non-commercial terms. Chosen deliberately for this research corpus; see the Plan 10 report.
MMS_MODEL = AlignmentModel(
    model_id="MahmoudAshraf/mms-300m-1130-forced-aligner",
    license="cc-by-nc-4.0",
    romanize=True,
)

#: The permissive profile: per-language Apache-2.0 checkpoints. Note this is *not* what
#: WhisperX uses for es/fr/de/it -- those are the VoxPopuli bundles, which are CC-BY-NC-4.0.
PERMISSIVE_MODELS: dict[str, AlignmentModel] = {
    "es": AlignmentModel(
        model_id="jonatasgrosman/wav2vec2-large-xlsr-53-spanish",
        license="apache-2.0",
        romanize=False,
        languages=("es",),
    ),
    "ru": AlignmentModel(
        model_id="jonatasgrosman/wav2vec2-large-xlsr-53-russian",
        license="apache-2.0",
        romanize=False,
        languages=("ru",),
    ),
    "en": AlignmentModel(
        model_id="jonatasgrosman/wav2vec2-large-xlsr-53-english",
        license="apache-2.0",
        romanize=False,
        languages=("en",),
    ),
}

ALIGNMENT_PROFILES = ("mms", "permissive")


def resolve_model(
    language: str,
    *,
    profile: str = "mms",
    allow_non_commercial: bool = True,
) -> AlignmentModel | None:
    """Return the model for ``language`` under ``profile``, or ``None`` when there is none.

    Returning ``None`` rather than falling back to another language's checkpoint is
    deliberate: aligning text against a model trained on a different language produces
    confident nonsense rather than an obvious failure.
    """
    if profile not in ALIGNMENT_PROFILES:
        raise ValueError(f"unknown alignment profile {profile!r}")
    model = MMS_MODEL if profile == "mms" else PERMISSIVE_MODELS.get(language)
    if model is None or not model.supports(language):
        return None
    if not allow_non_commercial and not model.commercial_use_allowed:
        return None
    return model
