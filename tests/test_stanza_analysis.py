import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from test_morphology import add_text

from speech_retrieval import analysis
from speech_retrieval.analysis import (
    InvalidAnalysisError,
    UnsupportedAnalysisError,
    clear_analyzer_cache,
    get_analyzer,
)
from speech_retrieval.indexing import build_index
from speech_retrieval.search import Corpus


@pytest.fixture
def fake_stanza(tmp_path, monkeypatch):
    """Exercise the real adapter and offline configuration without optional weights."""
    clear_analyzer_cache()
    package = ModuleType("stanza")
    common = ModuleType("stanza.resources.common")
    common.DEFAULT_RESOURCES_VERSION = "test-resources"  # type: ignore[attr-defined]
    resources = {
        lang: {
            "packages": {
                "default_fast": {
                    "tokenize": "fixture",
                    "pos": "fixture",
                    "lemma": "fixture",
                    **({"mwt": "fixture"} if lang == "es" else {}),
                }
            }
        }
        for lang in ("es", "ja", "ko", "zh-hans")
    }
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "resources.json").write_text(json.dumps(resources))

    def document(text):
        tokens = []
        if text == "al":
            words = [
                SimpleNamespace(text="a", lemma="a", upos="ADP", feats=None),
                SimpleNamespace(text="el", lemma="el", upos="DET", feats="Gender=Masc|Number=Sing"),
            ]
            tokens.append(SimpleNamespace(start_char=0, end_char=2, words=words))
        else:
            # Character segmentation intentionally differs from the regex tokenizer.
            for i, char in enumerate(text):
                if char.isspace() or char in ".。":
                    continue
                word = SimpleNamespace(
                    text=char, lemma=char, upos="NOUN", feats=None, start_char=i, end_char=i + 1
                )
                tokens.append(SimpleNamespace(start_char=i, end_char=i + 1, words=[word]))
        return SimpleNamespace(sentences=[SimpleNamespace(tokens=tokens)])

    pipeline = Mock(side_effect=lambda **_: Mock(side_effect=document))
    package.Pipeline = pipeline  # type: ignore[attr-defined]
    package.download = Mock()  # type: ignore[attr-defined]
    common.download_resources_json = Mock(  # type: ignore[attr-defined]
        side_effect=lambda directory: (Path(directory) / "resources.json").write_text(
            json.dumps(resources)
        )
    )  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "stanza", package)
    monkeypatch.setitem(sys.modules, "stanza.resources", ModuleType("stanza.resources"))
    monkeypatch.setitem(sys.modules, "stanza.resources.common", common)
    real_version = analysis.version
    monkeypatch.setattr(
        analysis, "version", lambda name: "test-stanza" if name == "stanza" else real_version(name)
    )
    yield model_dir, pipeline, package, common
    clear_analyzer_cache()


def test_stanza_expansion_uses_shared_parent_offsets_and_nullable_features(fake_stanza):
    model_dir, pipeline, _, _ = fake_stanza
    analyzer = get_analyzer("es", "stanza", str(model_dir))
    result = analyzer.analyze("al")
    assert [t.lemma for t in result.tokens] == ["a", "el"]
    assert all(
        t.surface == "al" and t.shared_span and (t.start, t.end) == (0, 2) for t in result.tokens
    )
    assert result.tokens[0].features is None
    assert result.tokens[1].features == {"Gender": "Masc", "Number": "Sing"}
    config = pipeline.call_args.kwargs
    assert config["download_method"] is None
    assert config["package"] is None
    assert config["use_gpu"] is False
    assert set(config["processors"]) == {"tokenize", "mwt", "pos", "lemma"}
    assert config["pos_batch_size"] == config["lemma_batch_size"] == 256


@pytest.mark.parametrize(
    "language,text", [("ja", "東京大学"), ("ko", "한국학교"), ("zh", "北京大学")]
)
def test_non_whitespace_phrase_offsets_surface_preservation_and_reuse(
    tmp_path, fake_stanza, language, text
):
    model_dir, pipeline, _, _ = fake_stanza
    add_text(tmp_path, text + "。", language=language)
    build_index(data_dir=tmp_path, models_dir=model_dir)
    corpus = Corpus(tmp_path, models_dir=model_dir)
    for mode in ("auto", "exact", "lemma"):
        result = corpus.search(text[1:3], source_language=language, match_mode=mode)
        assert result["total_occurrences"] == 1
        match = result["results"][0]["match"]
        assert (match["char_start"], match["char_end"]) == (1, 3)
        assert result["results"][0]["matched_surface"] == text[1:3]
    assert (
        corpus.search(text, source_language=language, match_mode="exact")["total_occurrences"] == 1
    )
    assert pipeline.call_count == 1
    assert "mwt" not in pipeline.call_args.kwargs["processors"]


def test_missing_stanza_models_never_download_and_exact_remains_usable(tmp_path, fake_stanza):
    model_dir, pipeline, package, _ = fake_stanza
    add_text(tmp_path, "東京大学。", language="ja")
    build_index(data_dir=tmp_path, models_dir=model_dir)
    clear_analyzer_cache()
    pipeline.side_effect = FileNotFoundError("model not found")
    corpus = Corpus(tmp_path, models_dir=model_dir)
    result = corpus.search("東京大学", source_language="ja")
    assert result["total_occurrences"] == 1
    assert result["morphology_available"] is False
    assert (
        corpus.search("東京大学", source_language="ja", match_mode="exact")["total_occurrences"]
        == 1
    )
    with pytest.raises(UnsupportedAnalysisError):
        corpus.search("東京大学", source_language="ja", match_mode="lemma")
    package.download.assert_not_called()


def test_download_is_explicit_and_resolves_only_required_processors(tmp_path, fake_stanza):
    _, _, package, common = fake_stanza
    destination = tmp_path / "downloaded"
    result = analysis.download_models("zh", destination)
    assert result["analyzer"]["name"] == "stanza"
    common.download_resources_json.assert_called_once_with(str(destination))
    config = package.download.call_args.kwargs
    assert config["lang"] == "zh-hans"
    assert config["package"] is None
    assert set(config["processors"]) == {"tokenize", "pos", "lemma"}


def test_stanza_invalid_reconstructed_span_is_clear(fake_stanza):
    model_dir, pipeline, _, _ = fake_stanza
    word = SimpleNamespace(
        text="bad", lemma="bad", upos="NOUN", feats=None, start_char=-1, end_char=99
    )
    pipeline.return_value = lambda _: SimpleNamespace(
        sentences=[SimpleNamespace(tokens=[SimpleNamespace(words=[word])])]
    )
    pipeline.side_effect = None
    with pytest.raises(InvalidAnalysisError, match="span"):
        get_analyzer("ja", "stanza", str(model_dir)).analyze("東京")


@pytest.mark.parametrize(
    "language,text",
    [
        ("ja", "私は東京の大学で勉強します。"),
        ("ko", "나는 학교에서 공부합니다."),
        ("zh", "我在北京大学学习。"),
    ],
)
def test_real_local_cjk_models(tmp_path, language, text):
    pytest.importorskip("stanza", reason="Install the nlp extra for real CJK integration")
    models = Path(
        os.environ.get("SPEECH_RETRIEVAL_TEST_MODELS_DIR", "data/models/stanza")
    ).resolve()
    clear_analyzer_cache()
    try:
        analyzer = get_analyzer(language, "stanza", str(models))
    except UnsupportedAnalysisError as error:
        pytest.skip(str(error))
    analyzed = analyzer.analyze(text)
    assert len(analyzed.tokens) > 1
    assert all(text[t.start : t.end] == t.surface for t in analyzed.tokens)
    add_text(tmp_path, text, language=language)
    build_index(data_dir=tmp_path, analyzer="stanza", models_dir=models)
    # Use the analyzer's first two source words as a contiguous phrase.
    phrase = text[analyzed.tokens[0].start : analyzed.tokens[1].end]
    result = Corpus(tmp_path, models_dir=models).search(phrase, source_language=language)
    assert result["total_occurrences"] >= 1
    assert result["results"][0]["matched_surface"] == phrase


def test_suggestions_use_native_words_without_legacy_unsegmented_run(tmp_path, fake_stanza):
    model_dir, _, _, _ = fake_stanza
    add_text(tmp_path, "東京大学。", language="ja")
    build_index(data_dir=tmp_path, models_dir=model_dir)
    suggestions = Corpus(tmp_path, models_dir=model_dir).suggestions(source_language="ja", limit=30)
    assert suggestions
    assert not any(item["text"] == "東京大学" for item in suggestions)
    assert any(item["text"] == "東" for item in suggestions)
