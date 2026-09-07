import importlib.util

import pytest

from speech_retrieval.audio_scoring import (
    SCORING_NORMALIZERS,
    ScoringError,
    align_sequences,
    default_normalization,
    get_normalizer,
    jiwer_reference_counts,
    score_disagreement,
)


def counts(reference, hypothesis, **kwargs):
    score = score_disagreement(reference_text=reference, hypothesis_text=hypothesis, **kwargs)
    return (score.hits, score.substitutions, score.deletions, score.insertions)


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected", "rate"),
    [
        ("hola que tal", "hola que tal", (3, 0, 0, 0), 0.0),
        ("hola que tal", "hola tal", (2, 0, 1, 0), 1 / 3),
        ("hola que tal", "hola que tal amigo", (3, 0, 0, 1), 1 / 3),
        ("hola que tal", "hola qué tal", (2, 1, 0, 0), 1 / 3),
        ("uno dos tres", "cero uno dos", (2, 0, 1, 1), 2 / 3),
        ("", "", (0, 0, 0, 0), 0.0),
        ("hola", "", (0, 0, 1, 0), 1.0),
    ],
)
def test_word_scoring_reports_exact_counts_not_only_rates(reference, hypothesis, expected, rate):
    score = score_disagreement(reference_text=reference, hypothesis_text=hypothesis, language="es")

    assert (score.hits, score.substitutions, score.deletions, score.insertions) == expected
    assert score.error_rate == pytest.approx(rate)
    assert score.metric == "wer" and score.unit == "word"
    assert score.reference_length == len(score.reference_tokens)
    assert score.hypothesis_length == len(score.hypothesis_tokens)


def test_an_empty_reference_has_no_denominator_and_is_not_reported_as_zero_error():
    score = score_disagreement(reference_text="", hypothesis_text="hola", language="es")

    assert score.error_rate is None
    assert score.insertions == 1
    assert score.agreement == 0.0


def test_error_rate_may_exceed_one_and_bounded_agreement_is_named_separately():
    score = score_disagreement(
        reference_text="hola", hypothesis_text="hola que tal amigo mio", language="es"
    )

    assert score.error_rate == pytest.approx(4.0)
    assert score.agreement == 0.0


def test_word_normalization_preserves_diacritics_and_digits_and_folds_punctuation():
    normalizer = get_normalizer("word-v1")

    assert normalizer.normalize("¡Hola, señor! ¿Qué tal… 25?") == "hola señor qué tal 25"
    assert normalizer.tokenize("d’acord — está") == ["d'acord", "está"]
    assert normalizer.normalize("está") != normalizer.normalize("esta")
    assert normalizer.payload()["preserves_diacritics"] is True


def test_accent_folding_is_only_available_as_a_named_sensitivity_analysis():
    folded = score_disagreement(
        reference_text="hola qué tal",
        hypothesis_text="hola que tal",
        normalization_version="word-accent-folded-v1",
    )

    assert folded.error_rate == 0.0
    assert folded.normalization_version == "word-accent-folded-v1"
    assert folded.normalizer["preserves_diacritics"] is False
    assert SCORING_NORMALIZERS["word-accent-folded-v1"].fold_accents is True


def test_filler_words_and_numerals_are_deliberately_not_equated():
    assert counts("eh hola que tal", "hola que tal", language="es") == (3, 0, 1, 0)
    assert counts("veinticinco", "25", language="es") == (0, 1, 0, 0)


def test_character_scoring_removes_spaces_and_punctuation_per_configured_language():
    score = score_disagreement(reference_text="你好，吗?", hypothesis_text="你好 啊", language="zh")

    assert score.metric == "cer" and score.unit == "character"
    assert score.normalized_reference == "你好吗"
    assert score.normalized_hypothesis == "你好啊"
    assert (score.hits, score.substitutions) == (2, 1)
    assert score.error_rate == pytest.approx(1 / 3)
    assert default_normalization("ja") == "character-v1"
    assert default_normalization("es-MX") == "word-v1"


def test_alignment_chunks_locate_every_discrepancy_for_review():
    score = score_disagreement(
        reference_text="uno dos tres cuatro",
        hypothesis_text="uno DOS tres",
        language="es",
    )
    operations = [chunk.operation for chunk in score.alignment]

    assert operations == ["equal", "delete"]
    last = score.alignment[-1]
    assert (last.reference_start, last.reference_end) == (3, 4)
    assert score.reference_tokens[last.reference_start] == "cuatro"


def test_scoring_rejects_unknown_normalization_versions_and_missing_language():
    with pytest.raises(ScoringError, match="unknown normalization version"):
        score_disagreement(reference_text="a", hypothesis_text="a", normalization_version="v9")
    with pytest.raises(ScoringError, match="language or normalization_version"):
        score_disagreement(reference_text="a", hypothesis_text="a")


def test_alignment_is_symmetric_in_its_definition_of_insertions_and_deletions():
    forward = align_sequences(["a", "b"], ["a"])
    backward = align_sequences(["a"], ["a", "b"])

    assert (forward.deletions, forward.insertions) == (1, 0)
    assert (backward.deletions, backward.insertions) == (0, 1)


@pytest.mark.skipif(importlib.util.find_spec("jiwer") is None, reason="jiwer is not installed")
@pytest.mark.parametrize(
    ("reference", "hypothesis"),
    [
        ("hola que tal", "hola que tal"),
        ("hola que tal", "hola tal"),
        ("hola que tal", "hola que tal amigo"),
        ("uno dos tres", "cero uno dos"),
    ],
)
def test_word_counts_agree_with_the_optional_jiwer_scorer(reference, hypothesis):
    score = score_disagreement(reference_text=reference, hypothesis_text=hypothesis, language="es")

    assert jiwer_reference_counts(list(score.reference_tokens), list(score.hypothesis_tokens)) == {
        "hits": score.hits,
        "substitutions": score.substitutions,
        "deletions": score.deletions,
        "insertions": score.insertions,
    }


def test_jiwer_cross_check_is_optional():
    if importlib.util.find_spec("jiwer") is None:
        assert jiwer_reference_counts(["a"], ["a"]) is None
    else:
        assert jiwer_reference_counts(["a"], ["a"]) == {
            "hits": 1,
            "substitutions": 0,
            "deletions": 0,
            "insertions": 0,
        }
