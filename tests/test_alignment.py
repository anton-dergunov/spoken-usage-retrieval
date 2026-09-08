"""Alignment types, invariants, and the CTC mapping, all without a downloaded model.

Emissions are generated rather than recorded: a stub backend produces log probabilities that
spell a known token sequence at known frames, so every mapping and grouping rule can be
asserted exactly. The model-backed path is exercised separately by the experiment.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from speech_retrieval.alignment import (
    ALIGNMENT_SCHEMA_VERSION,
    MMS_MODEL,
    PERMISSIVE_MODELS,
    AlignedGroup,
    AlignmentGroupError,
    AlignmentModel,
    AlignmentResult,
    coverage_of,
    failed_result,
    resolve_model,
    settings_hash,
    unavailable_result,
    validate_groups,
)

torch = pytest.importorskip("torch", reason="alignment extra not installed")

from speech_retrieval.alignment_ctc import (  # noqa: E402
    Emissions,
    TimedWord,
    Wav2Vec2ForcedAligner,
    build_groups,
    identity_map,
    merge_overlaps,
    plan_tokens,
    read_wave,
    vocabulary_key,
)

LOWER_VOCAB = {"<pad>": 0, "|": 1, **{c: i + 2 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}}
NO_DELIMITER_VOCAB = {
    "<blank>": 0,
    **{c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
}


def emissions_for(
    tokens: list[int],
    vocab: dict[str, int],
    *,
    blank: int = 0,
    delimiter: str | None = "|",
    frames_per_token: int = 3,
    frame_rate: float = 50.0,
) -> Emissions:
    """Build log probabilities that make ``tokens`` the overwhelmingly likely alignment."""
    total = len(tokens) * frames_per_token
    log_probs = torch.full((1, total, len(vocab)), -20.0)
    for index, token in enumerate(tokens):
        for offset in range(frames_per_token):
            log_probs[0, index * frames_per_token + offset, token] = 0.0
    return Emissions(
        log_probs=torch.log_softmax(log_probs, dim=-1),
        frame_rate=frame_rate,
        vocab=vocab,
        blank_id=blank,
        word_delimiter=delimiter,
    )


class StubBackend:
    """A CTC backend whose emissions are supplied by the test, not by a model."""

    def __init__(self, model: AlignmentModel, emissions: Emissions) -> None:
        self._model = model
        self._emissions = emissions

    @property
    def model(self) -> AlignmentModel:
        return self._model

    @property
    def device(self) -> str:
        return "cpu"

    def package_versions(self) -> dict[str, str]:
        return {"stub": "1.0"}

    def emissions(self, samples: object, sample_rate: int) -> Emissions:
        return self._emissions


# --- Group invariants -------------------------------------------------------------------


def group(text: str, start: int, timed: bool = True) -> AlignedGroup:
    return AlignedGroup(
        text=text,
        char_start=start,
        char_end=start + len(text),
        start=0.5 if timed else None,
        end=1.0 if timed else None,
        match_status="matched" if timed else "unmatched",
    )


def test_validate_groups_accepts_a_complete_ordered_cover() -> None:
    text = "one two"
    validate_groups([group("one", 0), group(" ", 3, timed=False), group("two", 4)], text)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([group("one", 0)], "cover"),
        ([group("one", 1), group(" two", 4, timed=False)], "expected 0"),
        (
            [group("one", 0), group(" ", 3, timed=False), group("txo", 4)],
            "does not match its character range",
        ),
    ],
)
def test_validate_groups_rejects_a_broken_cover(groups: list[AlignedGroup], message: str) -> None:
    with pytest.raises(AlignmentGroupError, match=message):
        validate_groups(groups, "one two")


def test_validate_groups_rejects_a_half_timed_group() -> None:
    partial = AlignedGroup(
        text="one two",
        char_start=0,
        char_end=7,
        start=1.0,
        end=None,
        match_status="matched",
    )
    with pytest.raises(AlignmentGroupError, match="partially timed"):
        validate_groups([partial], "one two")


def test_validate_groups_rejects_a_group_reaching_past_the_text() -> None:
    beyond = AlignedGroup(
        text="one two",
        char_start=0,
        char_end=99,
        start=None,
        end=None,
        match_status="unmatched",
    )
    with pytest.raises(AlignmentGroupError, match="beyond"):
        validate_groups([beyond], "one two")


def test_coverage_ignores_punctuation_so_a_punctuated_sentence_can_reach_one() -> None:
    text = "hola, ¿qué tal?"
    groups = build_groups(
        text,
        [
            TimedWord(0, 4, 0.0, 0.4, 0.9),
            TimedWord(7, 10, 0.5, 0.8, 0.9),
            TimedWord(11, 14, 0.9, 1.2, 0.9),
        ],
    )
    validate_groups(groups, text)
    assert coverage_of(groups, text) == 1.0


def test_coverage_reports_the_matchable_share_when_a_word_is_dropped() -> None:
    text = "uno dos"
    groups = build_groups(text, [TimedWord(0, 3, 0.0, 0.3, 0.9)])
    assert coverage_of(groups, text) == pytest.approx(3 / 6)


def test_coverage_of_text_with_no_matchable_characters_is_zero() -> None:
    assert coverage_of([], "!!!") == 0.0


# --- Grouping ---------------------------------------------------------------------------


def test_build_groups_marks_gaps_as_punctuation_or_unmatched() -> None:
    text = "uno, dos tres"
    groups = build_groups(text, [TimedWord(0, 3, 0.0, 0.3, 0.9), TimedWord(5, 8, 0.4, 0.7, 0.9)])
    validate_groups(groups, text)
    statuses = [(item.text, item.match_status) for item in groups]
    assert statuses == [
        ("uno", "matched"),
        (", ", "punctuation"),
        ("dos", "matched"),
        (" tres", "unmatched"),
    ]


def test_unmatched_groups_carry_no_time_rather_than_an_interpolated_guess() -> None:
    groups = build_groups("uno dos", [TimedWord(0, 3, 0.0, 0.3, 0.9)])
    trailing = groups[-1]
    assert trailing.match_status == "unmatched"
    assert trailing.start is None and trailing.end is None


def test_merge_overlaps_collapses_spans_that_share_source_characters() -> None:
    merged = merge_overlaps([TimedWord(0, 5, 0.0, 0.4, 0.8), TimedWord(3, 8, 0.4, 0.9, 0.6)])
    assert len(merged) == 1
    assert (merged[0].source_start, merged[0].source_end) == (0, 8)
    assert (merged[0].start, merged[0].end) == (0.0, 0.9)
    assert merged[0].confidence == pytest.approx(0.7)


def test_merge_overlaps_keeps_disjoint_spans_separate_and_ordered() -> None:
    merged = merge_overlaps([TimedWord(6, 9, 0.5, 0.8, 0.9), TimedWord(0, 3, 0.0, 0.3, 0.9)])
    assert [word.source_start for word in merged] == [0, 6]


# --- Vocabulary mapping -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("char", "vocab", "expected"),
    [
        ("a", LOWER_VOCAB, "a"),
        ("A", LOWER_VOCAB, "a"),
        ("á", LOWER_VOCAB, "a"),
        ("Ñ", LOWER_VOCAB, "n"),
        ("E", {"E": 5}, "E"),
        ("!", LOWER_VOCAB, None),
        ("ж", LOWER_VOCAB, None),
    ],
)
def test_vocabulary_key_folds_case_and_accents_before_giving_up(
    char: str, vocab: dict[str, int], expected: str | None
) -> None:
    assert vocabulary_key(char, vocab) == expected


def test_plan_tokens_inserts_a_delimiter_between_words_when_the_vocabulary_has_one() -> None:
    emissions = emissions_for([], LOWER_VOCAB)
    plan = plan_tokens(identity_map("ab cd"), emissions)
    assert plan.token_ids == [LOWER_VOCAB[c] for c in ["a", "b", "|", "c", "d"]]
    assert plan.word_index == [0, 0, -1, 1, 1]


def test_plan_tokens_still_separates_words_when_the_vocabulary_has_no_delimiter() -> None:
    """MMS has no delimiter token, so word boundaries must come from the index, not the stream."""
    emissions = emissions_for([], NO_DELIMITER_VOCAB, blank=0, delimiter=None)
    plan = plan_tokens(identity_map("ab cd"), emissions)
    assert plan.token_ids == [NO_DELIMITER_VOCAB[c] for c in ["a", "b", "c", "d"]]
    assert plan.word_index == [0, 0, 1, 1]


def test_plan_tokens_drops_unmappable_characters_but_keeps_word_numbering() -> None:
    emissions = emissions_for([], LOWER_VOCAB)
    plan = plan_tokens(identity_map("¡ab! cd"), emissions)
    assert plan.word_index == [0, 0, -1, 1, 1]
    assert [span for span in plan.spans if span != (-1, -1)] == [(1, 2), (2, 3), (5, 6), (6, 7)]


# --- End to end over stub emissions ---------------------------------------------------------


def aligner_for(text: str, vocab: dict[str, int], *, delimiter: str | None = "|") -> tuple:
    model = AlignmentModel(model_id="stub", license="apache-2.0", romanize=False)
    probe = emissions_for([], vocab, delimiter=delimiter)
    plan = plan_tokens(identity_map(text), probe)
    emissions = emissions_for(plan.token_ids, vocab, delimiter=delimiter)
    return Wav2Vec2ForcedAligner(StubBackend(model, emissions)), emissions


def test_alignment_over_stub_emissions_times_each_word_in_order() -> None:
    text = "uno dos tres"
    aligner, emissions = aligner_for(text, LOWER_VOCAB)
    groups, confidence = aligner.align_to_emissions(text, "es", emissions)
    validate_groups(groups, text)
    matched = [item for item in groups if item.match_status == "matched"]
    assert [item.text for item in matched] == ["uno", "dos", "tres"]
    assert [item.start for item in matched] == sorted(item.start for item in matched)
    assert confidence > 0.9
    assert coverage_of(groups, text) == 1.0


def test_alignment_preserves_punctuation_and_reconstructs_the_source_exactly() -> None:
    text = "hola, ¿que tal?"
    aligner, emissions = aligner_for(text, LOWER_VOCAB)
    groups, _ = aligner.align_to_emissions(text, "es", emissions)
    validate_groups(groups, text)
    assert "".join(item.text for item in groups) == text
    assert [item.text for item in groups if item.match_status == "matched"] == [
        "hola",
        "que",
        "tal",
    ]


def test_alignment_handles_a_repeated_word_as_two_separate_groups() -> None:
    text = "no no no"
    aligner, emissions = aligner_for(text, LOWER_VOCAB)
    groups, _ = aligner.align_to_emissions(text, "es", emissions)
    validate_groups(groups, text)
    matched = [item for item in groups if item.match_status == "matched"]
    assert len(matched) == 3
    assert [item.char_start for item in matched] == [0, 3, 6]


def test_alignment_without_a_delimiter_token_still_splits_words() -> None:
    text = "uno dos"
    aligner, emissions = aligner_for(text, NO_DELIMITER_VOCAB, delimiter=None)
    groups, _ = aligner.align_to_emissions(text, "es", emissions)
    matched = [item for item in groups if item.match_status == "matched"]
    assert [item.text for item in matched] == ["uno", "dos"]


def test_alignment_refuses_text_with_no_mappable_characters() -> None:
    aligner, emissions = aligner_for("uno", LOWER_VOCAB)
    with pytest.raises(ValueError, match="no source character"):
        aligner.align_to_emissions("!!!", "es", emissions)


def test_alignment_refuses_more_tokens_than_frames() -> None:
    text = "uno"
    aligner, _ = aligner_for(text, LOWER_VOCAB)
    short = emissions_for([2], LOWER_VOCAB, frames_per_token=1)
    with pytest.raises(ValueError, match="do not fit"):
        aligner.align_to_emissions("uno dos tres cuatro", "es", short)


def test_alignment_refuses_emissions_with_no_frame_rate() -> None:
    text = "uno"
    aligner, emissions = aligner_for(text, LOWER_VOCAB)
    with pytest.raises(ValueError, match="frame rate"):
        aligner.align_to_emissions(text, "es", replace(emissions, frame_rate=0.0))


def test_align_reports_audio_missing_without_raising(tmp_path) -> None:
    aligner, _ = aligner_for("uno", LOWER_VOCAB)
    result = aligner.align("uno", "es", tmp_path / "absent.wav")
    assert result.status == "unavailable"
    assert result.reason == "audio_missing"
    assert result.provenance is not None


def test_align_reports_empty_text_without_raising(tmp_path) -> None:
    aligner, _ = aligner_for("uno", LOWER_VOCAB)
    assert aligner.align("   ", "es", tmp_path / "absent.wav").reason == "text_empty"


def test_provenance_always_records_the_model_license() -> None:
    aligner, _ = aligner_for("uno", LOWER_VOCAB)
    assert aligner.provenance.model_license == "apache-2.0"
    assert aligner.provenance.settings_hash


# --- Result types -------------------------------------------------------------------------


def test_unavailable_and_failed_results_carry_no_timing() -> None:
    for result in (unavailable_result("dependency_missing"), failed_result("boom")):
        assert result.groups == ()
        assert not result.usable
        assert result.timed_text() == []
        assert result.schema_version == ALIGNMENT_SCHEMA_VERSION


def test_timed_text_drops_untimed_groups_for_the_player() -> None:
    text = "uno dos"
    groups = build_groups(text, [TimedWord(0, 3, 0.0, 0.3, 0.9)])
    result = AlignmentResult(status="partial", groups=tuple(groups), coverage=0.5)
    assert [item["text"] for item in result.timed_text()] == ["uno"]
    assert result.usable


def test_result_round_trips_through_a_plain_dictionary() -> None:
    groups = build_groups("uno", [TimedWord(0, 3, 0.0, 0.3, 0.9)])
    result = AlignmentResult(status="complete", groups=tuple(groups), coverage=1.0)
    payload = result.as_dict()
    assert payload["status"] == "complete"
    assert payload["groups"][0]["char_end"] == 3


def test_settings_hash_is_stable_and_order_independent() -> None:
    assert settings_hash({"a": 1, "b": 2}) == settings_hash({"b": 2, "a": 1})
    assert settings_hash({"a": 1}) != settings_hash({"a": 2})


# --- Model registry -------------------------------------------------------------------------


def test_the_default_profile_is_mms_and_is_recorded_as_non_commercial() -> None:
    model = resolve_model("es")
    assert model is not None
    assert model.model_id == MMS_MODEL.model_id
    assert model.license == "cc-by-nc-4.0"
    assert not model.commercial_use_allowed


def test_mms_covers_every_language_while_permissive_is_per_language() -> None:
    assert resolve_model("sw") is not None
    assert resolve_model("sw", profile="permissive") is None
    for language in ("es", "en", "ru"):
        model = resolve_model(language, profile="permissive")
        assert model is not None
        assert model.license == "apache-2.0"
        assert model.commercial_use_allowed


def test_selecting_the_permissive_profile_is_a_one_setting_change() -> None:
    assert resolve_model("ru", profile="permissive") == PERMISSIVE_MODELS["ru"]


def test_refusing_non_commercial_models_refuses_rather_than_falling_back() -> None:
    assert resolve_model("es", allow_non_commercial=False) is None
    assert resolve_model("es", profile="permissive", allow_non_commercial=False) is not None


def test_an_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown alignment profile"):
        resolve_model("es", profile="whatever")


def test_only_mms_romanizes_because_only_its_vocabulary_is_romanized() -> None:
    assert MMS_MODEL.romanize
    assert not any(model.romanize for model in PERMISSIVE_MODELS.values())


# --- Audio reading ----------------------------------------------------------------------------


def test_read_wave_returns_mono_float_samples(tmp_path) -> None:
    from audio_fixtures import write_wave

    path = write_wave(tmp_path / "clip.wav", seconds=0.5, sample_rate=16_000)
    samples, rate = read_wave(path)
    assert rate == 16_000
    assert len(samples) == 8_000
    assert max(abs(float(value)) for value in samples) <= 1.0


def test_read_wave_rejects_a_non_sixteen_bit_clip(tmp_path) -> None:
    import wave as wave_module

    path = tmp_path / "eight.wav"
    with wave_module.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(16_000)
        handle.writeframes(bytes(100))
    with pytest.raises(ValueError, match="16-bit"):
        read_wave(path)


def test_emissions_frame_rate_matches_the_generated_fixture() -> None:
    emissions = emissions_for([2, 3], LOWER_VOCAB, frames_per_token=4, frame_rate=25.0)
    assert emissions.log_probs.shape[1] == 8
    assert math.isclose(emissions.frame_rate, 25.0)
