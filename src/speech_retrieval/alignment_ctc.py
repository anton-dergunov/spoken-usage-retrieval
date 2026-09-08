"""CTC forced alignment over a swappable emissions backend.

One algorithm, two model profiles. Both MMS and the permissive per-language checkpoints are
wav2vec2-family CTC models, and ``torchaudio.functional.forced_align`` is the same Viterbi
step for both, so this module carries a single aligner and swaps only the thing that produces
emissions. That keeps an ONNX backend a later addition rather than a rewrite.

Models load through ``transformers`` rather than ``torchaudio.pipelines`` on purpose:
torchaudio's pipeline and I/O surface has been moving (this repository already had to stop
using its audio I/O when that moved to TorchCodec), while ``forced_align`` is a stable
compiled op. ``transformers`` also gives one uniform vocabulary surface for every checkpoint.

Everything heavy is imported lazily, so importing this module without the ``alignment`` extra
raises nothing until a caller actually asks for a model.
"""

from __future__ import annotations

import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .alignment import (
    AlignedGroup,
    AlignmentGroupError,
    AlignmentModel,
    AlignmentProvenance,
    AlignmentResult,
    AlignmentStatus,
    MatchStatus,
    coverage_of,
    failed_result,
    settings_hash,
    unavailable_result,
    validate_groups,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

ALIGNER_ID = "wav2vec2-ctc-forced-align-v1"

#: Feed the model at most this many seconds at once. wav2vec2 self-attention is quadratic in
#: frames, so a whole video in one pass exhausts memory; clips are already far shorter than
#: this, but the cap is enforced rather than assumed.
MAX_WINDOW_SECONDS = 30.0

#: Below this mean per-word CTC probability the alignment is reported as failed rather than
#: returned. Provisional until the Plan 10 experiment's sample review calibrates it.
MIN_MEAN_CONFIDENCE = 0.10

#: Below this share of matched characters the result is 'partial' rather than 'complete'.
MIN_COMPLETE_COVERAGE = 0.95


class AlignmentDependencyError(RuntimeError):
    """The optional ``alignment`` extra is not installed."""


@dataclass(frozen=True, slots=True)
class Emissions:
    """Frame-wise CTC log probabilities plus everything needed to interpret them."""

    log_probs: Any  # torch.Tensor of shape (1, frames, vocab)
    frame_rate: float
    vocab: dict[str, int]
    blank_id: int
    #: ``None`` for checkpoints with no word-delimiter token, such as MMS. Word boundaries are
    #: tracked alongside the token sequence either way, so this only decides whether a
    #: separator is fed to the model.
    word_delimiter: str | None


class CtcEmissionBackend(Protocol):
    """Produces CTC emissions for 16 kHz mono audio."""

    @property
    def model(self) -> AlignmentModel: ...

    @property
    def device(self) -> str: ...

    def package_versions(self) -> dict[str, str]: ...

    def emissions(self, samples: Any, sample_rate: int) -> Emissions: ...


# --- Source text mapping --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappedChar:
    """One character as the model sees it, and where it came from in the source string."""

    char: str
    source_start: int
    source_end: int


#: uroman expects ISO 639-3. Only the languages this corpus uses need an entry; anything else
#: romanizes without a language hint, which is uroman's documented default.
_UROMAN_LANGUAGES = {"es": "spa", "en": "eng", "ru": "rus"}


def uroman_language(language: str) -> str | None:
    return _UROMAN_LANGUAGES.get(language.split("-")[0].lower())


def identity_map(text: str) -> list[MappedChar]:
    return [MappedChar(char, index, index + 1) for index, char in enumerate(text)]


def romanized_map(text: str, romanizer: Any, language: str) -> list[MappedChar]:
    """Romanize ``text`` while keeping every output character's source span.

    ``uroman``'s edge format reports, for each romanized fragment, the span of *source*
    characters it came from. That is what makes MMS usable here: Cyrillic aligns through a
    Latin vocabulary while character offsets still point into the original Russian text. A
    single source character may expand to several romanized ones (``Щ`` becomes ``Shch``) or
    to none (``¿`` romanizes to nothing); both are handled by the span, not by position.
    """
    from uroman import RomFormat

    edges = romanizer.romanize_string(
        text, lcode=uroman_language(language), rom_format=RomFormat.EDGES
    )
    return [MappedChar(char, edge.start, edge.end) for edge in edges for char in edge.txt]


def vocabulary_key(char: str, vocab: dict[str, int]) -> str | None:
    """Return the vocabulary key for ``char``, or ``None`` when it has none.

    Checkpoints differ in case (``wav2vec2-base-960h`` is uppercase, the XLSR ones are
    lowercase) and in whether they keep accents, so try the character as written, then
    recased, then accent-stripped, before giving up.
    """
    for candidate in (char, char.lower(), char.upper()):
        if candidate in vocab:
            return candidate
    stripped = "".join(
        part for part in unicodedata.normalize("NFKD", char) if unicodedata.category(part) != "Mn"
    )
    for candidate in (stripped, stripped.lower(), stripped.upper()):
        if candidate and candidate in vocab:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class TokenPlan:
    """The target sequence for forced alignment, and its link back to the source string."""

    token_ids: list[int]
    #: Parallel to ``token_ids``. ``(source_start, source_end)`` for real characters, and
    #: ``(-1, -1)`` for an inserted word delimiter.
    spans: list[tuple[int, int]]
    #: Parallel to ``token_ids``. The index of the word each token belongs to, ``-1`` for a
    #: delimiter. Word grouping uses this rather than the delimiter token, because MMS has no
    #: delimiter in its vocabulary at all.
    word_index: list[int]


def plan_tokens(mapped: list[MappedChar], emissions: Emissions) -> TokenPlan:
    """Turn mapped source characters into a CTC target sequence.

    Characters with no vocabulary entry are dropped from the target but keep their place in
    the output, because the grouping step fills every gap explicitly.
    """
    token_ids: list[int] = []
    spans: list[tuple[int, int]] = []
    word_index: list[int] = []
    word = -1
    in_word = False
    for entry in mapped:
        if entry.char.isspace():
            in_word = False
            continue
        key = vocabulary_key(entry.char, emissions.vocab)
        if key is None:
            continue
        if not in_word:
            word += 1
            if token_ids and emissions.word_delimiter is not None:
                token_ids.append(emissions.vocab[emissions.word_delimiter])
                spans.append((-1, -1))
                word_index.append(-1)
            in_word = True
        token_ids.append(emissions.vocab[key])
        spans.append((entry.source_start, entry.source_end))
        word_index.append(word)
    return TokenPlan(token_ids=token_ids, spans=spans, word_index=word_index)


# --- Grouping -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimedWord:
    source_start: int
    source_end: int
    start: float
    end: float
    confidence: float


def build_groups(text: str, words: list[TimedWord]) -> list[AlignedGroup]:
    """Turn timed source spans into a complete, ordered cover of ``text``.

    Everything between the timed spans becomes an explicit untimed group, so the sequence
    reconstructs the source string exactly and a consumer can tell the difference between
    punctuation and text the model failed to match.
    """
    groups: list[AlignedGroup] = []
    cursor = 0

    def add_gap(start: int, end: int) -> None:
        if end <= start:
            return
        chunk = text[start:end]
        status: MatchStatus = (
            "punctuation" if not any(char.isalnum() for char in chunk) else "unmatched"
        )
        groups.append(
            AlignedGroup(
                text=chunk,
                char_start=start,
                char_end=end,
                start=None,
                end=None,
                match_status=status,
            )
        )

    for word in words:
        add_gap(cursor, word.source_start)
        groups.append(
            AlignedGroup(
                text=text[word.source_start : word.source_end],
                char_start=word.source_start,
                char_end=word.source_end,
                start=round(word.start, 3),
                end=round(word.end, 3),
                match_status="matched",
                confidence=round(word.confidence, 4),
            )
        )
        cursor = word.source_end
    add_gap(cursor, len(text))
    return groups


def merge_overlaps(words: list[TimedWord]) -> list[TimedWord]:
    """Collapse timed spans that share source characters, keeping the sequence ordered.

    Romanization can map one source character into tokens that land in adjacent words, so
    spans are not guaranteed disjoint. Merging keeps the group invariants satisfiable without
    discarding timing.
    """
    ordered = sorted(words, key=lambda word: (word.source_start, word.source_end))
    merged: list[TimedWord] = []
    for word in ordered:
        if merged and word.source_start < merged[-1].source_end:
            previous = merged[-1]
            merged[-1] = TimedWord(
                source_start=previous.source_start,
                source_end=max(previous.source_end, word.source_end),
                start=min(previous.start, word.start),
                end=max(previous.end, word.end),
                confidence=(previous.confidence + word.confidence) / 2,
            )
            continue
        merged.append(word)
    return merged


# --- Audio ----------------------------------------------------------------------------


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    """Read a prepared clip with the standard library.

    Plan 09 publishes clips as 16 kHz mono signed 16-bit PCM WAV, so no audio I/O library is
    needed -- and deliberately not torchaudio's, which moved to TorchCodec and broke this
    repository's feature extraction once already.
    """
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM, got {handle.getsampwidth() * 8}-bit")
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


# --- Backend --------------------------------------------------------------------------


def default_device(torch: Any) -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class TransformersCtcBackend:
    """Loads a wav2vec2 CTC checkpoint through ``transformers`` and emits log probabilities."""

    def __init__(
        self,
        model: AlignmentModel,
        *,
        device: str | None = None,
        num_threads: int | None = None,
    ) -> None:
        try:
            import torch
            from transformers import AutoFeatureExtractor, AutoTokenizer, Wav2Vec2ForCTC
        except ImportError as error:  # pragma: no cover - exercised by the extra-free suite
            raise AlignmentDependencyError(
                "the 'alignment' extra is required for forced alignment"
            ) from error

        if num_threads:
            torch.set_num_threads(num_threads)
        self._torch = torch
        self._model_spec = model
        self._device = device or default_device(torch)

        # Load the tokenizer and feature extractor directly rather than through AutoProcessor:
        # some checkpoints ship a Wav2Vec2ProcessorWithLM, which pulls in pyctcdecode for a
        # beam-search decoder this aligner never uses and fails to load without it.
        tokenizer = AutoTokenizer.from_pretrained(model.model_id)
        extractor = AutoFeatureExtractor.from_pretrained(model.model_id)
        self._net = Wav2Vec2ForCTC.from_pretrained(model.model_id).eval().to(self._device)

        self._vocab = dict(tokenizer.get_vocab())
        self._normalize = bool(getattr(extractor, "do_normalize", False))

        # The CTC blank is not always the tokenizer's pad token: MMS names it <blank> and its
        # tokenizer reports a different pad id. Prefer the explicit name, then the model
        # config, and never the tokenizer's own pad id.
        self._blank_id = (
            self._vocab["<blank>"]
            if "<blank>" in self._vocab
            else int(self._net.config.pad_token_id or 0)
        )

        # MMS has no word-delimiter token at all. Word boundaries are tracked alongside the
        # token sequence, so a missing delimiter is a normal configuration, not an error.
        delimiter = getattr(tokenizer, "word_delimiter_token", None)
        self._delimiter = (
            delimiter if isinstance(delimiter, str) and delimiter in self._vocab else None
        )

    @property
    def model(self) -> AlignmentModel:
        return self._model_spec

    @property
    def device(self) -> str:
        return self._device

    def package_versions(self) -> dict[str, str]:
        import torch
        import torchaudio
        import transformers

        return {
            "torch": torch.__version__,
            "torchaudio": torchaudio.__version__,
            "transformers": transformers.__version__,
        }

    def emissions(self, samples: Any, sample_rate: int) -> Emissions:
        torch = self._torch
        waveform = torch.as_tensor(samples, dtype=torch.float32)
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        duration = waveform.shape[-1] / sample_rate
        if duration > MAX_WINDOW_SECONDS:
            raise ValueError(
                f"clip is {duration:.1f}s; alignment windows are capped at {MAX_WINDOW_SECONDS}s"
            )
        if self._normalize:
            # Every checkpoint here was trained on zero-mean unit-variance input. Skipping
            # this degrades emissions quietly rather than failing, so it is applied, not
            # assumed.
            waveform = (waveform - waveform.mean()) / (waveform.std() + 1e-7)
        with torch.inference_mode():
            logits = self._net(waveform.to(self._device)).logits
            log_probs = torch.log_softmax(logits.float(), dim=-1).cpu()
        frames = int(log_probs.shape[1])
        return Emissions(
            log_probs=log_probs,
            frame_rate=frames / duration if duration else 0.0,
            vocab=self._vocab,
            blank_id=self._blank_id,
            word_delimiter=self._delimiter,
        )


# --- Aligner --------------------------------------------------------------------------


class Wav2Vec2ForcedAligner:
    """Times known source text against prepared audio using CTC forced alignment."""

    def __init__(self, backend: CtcEmissionBackend, *, romanizer: Any | None = None) -> None:
        self._backend = backend
        self._romanizer = romanizer
        self._settings = settings_hash(
            {
                "aligner": ALIGNER_ID,
                "max_window_seconds": MAX_WINDOW_SECONDS,
                "min_mean_confidence": MIN_MEAN_CONFIDENCE,
                "min_complete_coverage": MIN_COMPLETE_COVERAGE,
            }
        )

    @property
    def provenance(self) -> AlignmentProvenance:
        model = self._backend.model
        return AlignmentProvenance(
            aligner=ALIGNER_ID,
            model_id=model.model_id,
            model_license=model.license,
            device=self._backend.device,
            settings_hash=self._settings,
            package_versions=self._backend.package_versions(),
        )

    def _map_source(self, text: str, language: str) -> list[MappedChar]:
        if not self._backend.model.romanize:
            return identity_map(text)
        if self._romanizer is None:
            from uroman import Uroman

            self._romanizer = Uroman()
        return romanized_map(text, self._romanizer, language)

    def align(self, text: str, source_language: str, audio_clip: Any) -> AlignmentResult:
        provenance = self.provenance
        if not text.strip():
            return unavailable_result("text_empty", provenance=provenance)

        path = Path(getattr(audio_clip, "path", audio_clip))
        if not path.is_file():
            return unavailable_result("audio_missing", provenance=provenance)

        try:
            samples, sample_rate = read_wave(path)
            emissions = self._backend.emissions(samples, sample_rate)
        except AlignmentDependencyError:
            raise
        except (OSError, ValueError) as error:
            return failed_result(f"emissions_failed: {error}", provenance=provenance)

        try:
            groups, confidence = self.align_to_emissions(text, source_language, emissions)
        except AlignmentGroupError as error:
            return failed_result(f"invalid_groups: {error}", provenance=provenance)
        except ValueError as error:
            return failed_result(f"alignment_failed: {error}", provenance=provenance)

        if not any(group.timed for group in groups):
            return failed_result("no_tokens_matched", provenance=provenance)
        if confidence < MIN_MEAN_CONFIDENCE:
            return failed_result(
                f"low_confidence: mean {confidence:.3f} below {MIN_MEAN_CONFIDENCE}",
                provenance=provenance,
            )
        coverage = coverage_of(groups, text)
        status: AlignmentStatus = "complete" if coverage >= MIN_COMPLETE_COVERAGE else "partial"
        return AlignmentResult(
            status=status,
            groups=tuple(groups),
            coverage=coverage,
            provenance=provenance,
        )

    def align_to_emissions(
        self, text: str, source_language: str, emissions: Emissions
    ) -> tuple[list[AlignedGroup], float]:
        """The pure part: emissions plus text in, validated groups out."""
        import torch
        import torchaudio

        plan = plan_tokens(self._map_source(text, source_language), emissions)
        if not plan.token_ids:
            raise ValueError("no source character mapped to the model vocabulary")
        frames = int(emissions.log_probs.shape[1])
        if len(plan.token_ids) > frames:
            raise ValueError(f"{len(plan.token_ids)} tokens do not fit in {frames} frames")
        if emissions.frame_rate <= 0:
            raise ValueError("emissions carry no frame rate")

        targets = torch.tensor([plan.token_ids], dtype=torch.int32)
        aligned, scores = torchaudio.functional.forced_align(
            emissions.log_probs, targets, blank=emissions.blank_id
        )
        spans = torchaudio.functional.merge_tokens(aligned[0], scores[0].exp())
        if len(spans) != len(plan.token_ids):
            raise ValueError(f"{len(spans)} aligned spans for {len(plan.token_ids)} tokens")

        rate = emissions.frame_rate
        by_word: dict[int, list[tuple[tuple[int, int], Any]]] = {}
        for word, span_range, span in zip(plan.word_index, plan.spans, spans, strict=True):
            if word < 0:  # an inserted delimiter carries no source characters
                continue
            by_word.setdefault(word, []).append((span_range, span))

        words = [
            TimedWord(
                source_start=min(source_start for (source_start, _), _ in entries),
                source_end=max(source_end for (_, source_end), _ in entries),
                start=min(span.start for _, span in entries) / rate,
                end=max(span.end for _, span in entries) / rate,
                confidence=float(sum(span.score for _, span in entries) / len(entries)),
            )
            for entries in (by_word[key] for key in sorted(by_word))
        ]
        merged = merge_overlaps(words)
        groups = build_groups(text, merged)
        validate_groups(groups, text)
        mean_confidence = sum(word.confidence for word in merged) / len(merged) if merged else 0.0
        return groups, mean_confidence


def build_aligner(
    model: AlignmentModel,
    *,
    device: str | None = None,
    num_threads: int | None = None,
) -> Wav2Vec2ForcedAligner:
    """Load ``model`` and return an aligner using it."""
    backend = TransformersCtcBackend(model, device=device, num_threads=num_threads)
    return Wav2Vec2ForcedAligner(backend)
