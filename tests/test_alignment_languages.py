"""Language coverage for alignment: model resolution, scripts, and romanization.

These run without a corpus and without downloading a model, so a language can be proven wired up
before its channel catalogue exists. The real-model checks are opt-in, because they download
gigabytes; enable them with SPEECH_RETRIEVAL_ALIGNMENT_MODEL_TESTS=1.
"""

from __future__ import annotations

import os

import pytest

from speech_retrieval.alignment import (
    MMS_MODEL,
    PERMISSIVE_MODELS,
    resolve_model,
)

pytest.importorskip("torch", reason="alignment extra not installed")

from speech_retrieval.alignment_ctc import (  # noqa: E402
    Emissions,
    identity_map,
    plan_tokens,
    romanized_map,
    uroman_language,
    vocabulary_key,
)

#: Languages the roadmap intends to align. Spanish is measured; the rest are required and pending.
TARGET_LANGUAGES = ("es", "en", "ru")

LATIN_VOCAB = {
    "<pad>": 0,
    "|": 1,
    **{character: index + 2 for index, character in enumerate("abcdefghijklmnopqrstuvwxyz")},
}
CYRILLIC_VOCAB = {
    "<pad>": 0,
    "|": 1,
    **{character: index + 2 for index, character in enumerate("абвгдежзийклмнопрстуфхцчшщыэюя")},
}

SAMPLES = {
    "es": "¿Qué tal? Me llamo Ana.",
    "en": "Hello, my name is Ann.",
    "ru": "Привет, меня зовут Анна.",
}

RUN_MODEL_TESTS = os.environ.get("SPEECH_RETRIEVAL_ALIGNMENT_MODEL_TESTS") == "1"


def emissions(vocab: dict[str, int], *, delimiter: str | None = "|") -> Emissions:
    return Emissions(
        log_probs=None, frame_rate=50.0, vocab=vocab, blank_id=0, word_delimiter=delimiter
    )


@pytest.mark.parametrize("language", TARGET_LANGUAGES)
def test_every_target_language_resolves_under_both_profiles(language: str) -> None:
    default = resolve_model(language)
    assert default is not None and default.model_id == MMS_MODEL.model_id
    permissive = resolve_model(language, profile="permissive")
    assert permissive is not None, f"{language} has no Apache-2.0 checkpoint mapped"
    assert permissive.license == "apache-2.0"
    assert permissive.commercial_use_allowed


@pytest.mark.parametrize("language", TARGET_LANGUAGES)
def test_every_target_language_has_a_romanization_code(language: str) -> None:
    """MMS aligns through a romanized vocabulary, so an unmapped language would silently degrade."""
    assert uroman_language(language) is not None


def test_the_permissive_map_covers_exactly_the_target_languages() -> None:
    """Fails when a language is added to the roadmap without a permissive checkpoint."""
    assert set(PERMISSIVE_MODELS) == set(TARGET_LANGUAGES)


@pytest.mark.parametrize("language", TARGET_LANGUAGES)
def test_romanization_maps_every_sample_back_to_source_characters(language: str) -> None:
    """The MMS path is only usable if romanized characters still point into the original text."""
    uroman = pytest.importorskip("uroman")
    text = SAMPLES[language]
    mapped = romanized_map(text, uroman.Uroman(), language)
    assert mapped, "romanization produced nothing"
    for entry in mapped:
        assert 0 <= entry.source_start <= entry.source_end <= len(text)
    # Romanized output is Latin, whatever the source script.
    letters = [entry.char for entry in mapped if entry.char.isalpha()]
    assert letters and all(character.isascii() for character in letters)


def test_cyrillic_romanizes_to_latin_while_keeping_word_boundaries() -> None:
    """Russian is the case Spanish cannot exercise: a real transliteration, not accent folding."""
    uroman = pytest.importorskip("uroman")
    text = "Привет мир"
    mapped = romanized_map(text, uroman.Uroman(), "ru")
    plan = plan_tokens(mapped, emissions(LATIN_VOCAB))
    assert max(plan.word_index) == 1, "two words must stay two words through romanization"
    # The second word's tokens must point at the second word's characters in the Cyrillic source.
    second = [span for span, word in zip(plan.spans, plan.word_index, strict=True) if word == 1]
    assert min(start for start, _ in second) >= text.index("мир")


def test_a_native_script_checkpoint_needs_no_romanization() -> None:
    """The permissive Russian model has a Cyrillic vocabulary, so the text is used as written."""
    assert not PERMISSIVE_MODELS["ru"].romanize
    plan = plan_tokens(identity_map("привет мир"), emissions(CYRILLIC_VOCAB))
    assert max(plan.word_index) == 1
    assert len(plan.token_ids) == len("приветмир") + 1  # + the word delimiter


@pytest.mark.parametrize(
    ("character", "expected"),
    [
        ("Я", "я"),  # case folds
        ("ё", "е"),  # decomposes to a bare Cyrillic letter the vocabulary does have
        ("é", None),  # folding must not cross scripts into a vocabulary with no Latin
        ("Ñ", None),
        ("!", None),
    ],
)
def test_vocabulary_folding_stays_inside_the_checkpoints_script(
    character: str, expected: str | None
) -> None:
    """Case and accent folding is what makes a native-script checkpoint usable on real captions.

    It must not reach across scripts: a Cyrillic-only vocabulary has no sensible answer for a
    Latin letter, and inventing one would align the wrong sound.
    """
    assert vocabulary_key(character, CYRILLIC_VOCAB) == expected


@pytest.mark.parametrize("language", TARGET_LANGUAGES)
def test_an_unmapped_language_never_borrows_another_languages_model(language: str) -> None:
    assert resolve_model("xx", profile="permissive") is None
    assert resolve_model(language, profile="permissive") is not None


@pytest.mark.skipif(not RUN_MODEL_TESTS, reason="set SPEECH_RETRIEVAL_ALIGNMENT_MODEL_TESTS=1")
@pytest.mark.parametrize("language", TARGET_LANGUAGES)
@pytest.mark.parametrize("profile", ["mms", "permissive"])
def test_a_real_checkpoint_loads_and_exposes_a_usable_vocabulary(
    language: str, profile: str
) -> None:
    """Downloads weights. Proves the vocabulary can represent the language's own sample text."""
    from speech_retrieval.alignment_ctc import TransformersCtcBackend

    model = resolve_model(language, profile=profile)
    assert model is not None
    backend = TransformersCtcBackend(model, device="cpu")
    probe = Emissions(
        log_probs=None,
        frame_rate=50.0,
        vocab=backend._vocab,
        blank_id=0,
        word_delimiter=None,
    )
    text = SAMPLES[language]
    mapped = (
        romanized_map(text, __import__("uroman").Uroman(), language)
        if model.romanize
        else identity_map(text)
    )
    plan = plan_tokens(mapped, probe)
    assert plan.token_ids, f"{model.model_id} mapped no character of {language!r}"
    assert max(plan.word_index) >= 3
