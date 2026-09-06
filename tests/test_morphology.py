import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_index_search_api import add_video

from speech_retrieval import analysis as analyzers
from speech_retrieval import indexing, search
from speech_retrieval.analysis import (
    Analysis,
    AnalyzedToken,
    InvalidAnalysisError,
    SimplemmaAnalyzer,
    UnicodeAnalyzer,
    UnsupportedAnalysisError,
    clear_analyzer_cache,
)
from speech_retrieval.api import create_app
from speech_retrieval.indexing import build_index
from speech_retrieval.search import Corpus, IncompatibleIndexError, SearchError
from speech_retrieval.settings import Settings


def add_text(data_dir: Path, text: str, *, language="es", video="one", repeats=1):
    import hashlib

    add_video(data_dir, video, "Fixtures", language=language)
    for metadata_path in (data_dir / "raw" / "corpora" / language).glob("*/*/metadata.json"):
        metadata = json.loads(metadata_path.read_text())
        if metadata["video_id"] != video:
            continue
        payload = {
            "events": [
                {"tStartMs": 1000 + i * 6000, "dDurationMs": 3000, "segs": [{"utf8": text}]}
                for i in range(repeats)
            ]
        }
        caption_bytes = json.dumps(payload, ensure_ascii=False).encode()
        metadata["content_sha256"] = hashlib.sha256(caption_bytes).hexdigest()
        metadata["duration"] = 100
        metadata_path.write_text(json.dumps(metadata))
        metadata_path.with_name("subtitles.raw.json3").write_bytes(caption_bytes)


@pytest.fixture(autouse=True)
def clear_analyzers():
    clear_analyzer_cache()
    yield
    clear_analyzer_cache()


@pytest.mark.parametrize(
    "language,forms",
    [
        ("es", ["casa", "casas"]),
        ("es", ["bonito", "bonita", "bonitos", "bonitas"]),
        ("es", ["estar", "estoy", "estaba"]),
        ("en", ["cat", "cats"]),
        ("en", ["eat", "ate", "eaten"]),
    ],
)
def test_dictionary_and_inflected_forms_in_both_directions(tmp_path, language, forms):
    for i, form in enumerate(forms):
        add_text(tmp_path, f"{form}.", language=language, video=str(i))
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    for query in forms:
        response = corpus.search(query, source_language=language)
        assert response["match_mode"] == "auto"
        assert response["morphology_available"] is True
        assert {r["matched_surface"] for r in response["results"]} == set(forms)
        assert response["results"][0]["matched_surface"] == query
        assert response["results"][0]["match_type"] == "exact"
        assert response["totals_by_mode"] == {"exact": 1, "lemma": len(forms), "auto": len(forms)}
        assert corpus.search(query, source_language=language, match_mode="lemma")[
            "total_occurrences"
        ] == len(forms)
        assert (
            corpus.search(query, source_language=language, match_mode="exact")["total_occurrences"]
            == 1
        )
        for result in response["results"]:
            match = result["match"]
            assert (
                result["sentence"][match["char_start"] : match["char_end"]]
                == result["matched_surface"]
            )
            assert result["analyzer"]["name"] == "simplemma"
            assert result["token_analysis"][0]["upos"] is None
            assert result["token_analysis"][0]["features"] is None


def test_contiguous_ordered_phrases_and_five_word_limit(tmp_path):
    for i, text in enumerate(
        [
            "Las casas bonitas están aquí.",
            "Las bonitas casas están aquí.",
            "Las casas muy bonitas están aquí.",
        ]
    ):
        add_text(tmp_path, text, video=str(i))
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    response = corpus.search("casa bonita", source_language="es")
    assert response["total_occurrences"] == 1
    assert response["results"][0]["matched_surface"] == "casas bonitas"
    assert (
        corpus.search("Las casas bonitas están aquí", source_language="es", match_mode="exact")[
            "total_occurrences"
        ]
        == 1
    )
    with pytest.raises(SearchError, match="five"):
        corpus.search("uno dos tres cuatro cinco seis", source_language="es")


def test_token_position_search_matches_each_supported_ngram_length(tmp_path):
    terms = ["uno", "dos", "tres", "cuatro", "cinco"]
    add_text(tmp_path, " ".join(terms) + ".")
    build_index(data_dir=tmp_path, max_ngram=5)
    corpus = Corpus(tmp_path)
    for size in range(1, 6):
        query = " ".join(terms[:size])
        result = corpus.search(query, source_language="es", match_mode="exact")
        assert result["total_occurrences"] == 1
        assert result["results"][0]["matched_surface"] == query


def test_token_position_search_honors_built_ngram_limit(tmp_path):
    add_text(tmp_path, "uno dos tres.")
    build_index(data_dir=tmp_path, max_ngram=2)
    corpus = Corpus(tmp_path)
    assert (
        corpus.search("uno dos", source_language="es", match_mode="exact")["total_occurrences"] == 1
    )
    assert (
        corpus.search("uno dos tres", source_language="es", match_mode="exact")["total_occurrences"]
        == 0
    )


def test_ambiguity_reports_all_observed_lemmas_and_token_frequencies(tmp_path, monkeypatch):
    class ContextAnalyzer(SimplemmaAnalyzer):
        def analyze(self, text):
            result = super().analyze(text)
            return replace(
                result,
                tokens=tuple(
                    replace(t, lemma="ser" if "profesor" in text else "ir")
                    if t.normalized == "fui"
                    else t
                    for t in result.tokens
                ),
            )

    analyzer = ContextAnalyzer("es")
    monkeypatch.setattr(indexing, "get_analyzer", lambda *_: analyzer)
    monkeypatch.setattr(search, "recorded_analyzer", lambda *_: analyzer)
    add_text(tmp_path, "Yo fui profesor.", video="teacher", repeats=2)
    add_text(tmp_path, "Yo fui al colegio.", video="school")
    build_index(data_dir=tmp_path)
    response = Corpus(tmp_path).search("fui", source_language="es")
    analysis = response["query_analyses"][0]
    assert analysis["ambiguous"] is True
    assert {c["lemma"]: c["frequency"] for c in analysis["candidates"]} == {"ir": 1, "ser": 2}
    assert response["totals_by_mode"] == {"exact": 3, "lemma": 3, "auto": 3}
    assert all("corpus" in c["sources"] for c in analysis["candidates"])


def test_missing_lemma_does_not_create_incomplete_phrase_or_remove_surface(tmp_path, monkeypatch):
    class PartialAnalyzer(SimplemmaAnalyzer):
        def analyze(self, text):
            result = super().analyze(text)
            return replace(
                result,
                tokens=tuple(
                    replace(t, lemma=None) if t.normalized == "xxyy" else t for t in result.tokens
                ),
            )

    analyzer = PartialAnalyzer("es")
    monkeypatch.setattr(indexing, "get_analyzer", lambda *_: analyzer)
    monkeypatch.setattr(search, "recorded_analyzer", lambda *_: analyzer)
    add_text(tmp_path, "casas xxyy bonitas.")
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    assert corpus.search("casas xxyy bonitas", source_language="es")["total_occurrences"] == 1
    assert (
        corpus.search("casa xxyy bonita", source_language="es", match_mode="lemma")[
            "total_occurrences"
        ]
        == 0
    )
    assert (
        corpus.search("casa bonita", source_language="es", match_mode="lemma")["total_occurrences"]
        == 0
    )


def test_unsupported_language_modes_and_api_errors(tmp_path, monkeypatch):
    def no_stanza(*_):
        raise UnsupportedAnalysisError("No local model")

    monkeypatch.setattr(analyzers, "StanzaAnalyzer", no_stanza)
    add_text(tmp_path, "你好世界。", language="zh")
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    assert (
        corpus.search("你好世界", source_language="zh", match_mode="exact")["total_occurrences"]
        == 1
    )
    response = corpus.search("你好世界", source_language="zh")
    assert response["morphology_available"] is False
    assert response["morphology_unavailable_reason"] == "No local model"
    assert response["totals_by_mode"] == {"exact": 1, "lemma": 0, "auto": 1}
    with pytest.raises(UnsupportedAnalysisError):
        corpus.search("你好世界", source_language="zh", match_mode="lemma")
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get(
            "/api/v1/search",
            params={"q": "你好世界", "language": "zh", "match_mode": "lemma"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_analysis"
        assert (
            client.get(
                "/api/v1/search",
                params={"q": "你好世界", "language": "zh", "match_mode": "wrong"},
            ).status_code
            == 400
        )


def test_exact_first_even_with_diversification_and_repeated_sentence_matches(tmp_path):
    add_text(tmp_path, "casas bonitas.", video="exact", repeats=3)
    add_text(tmp_path, "Una casa muy bonita existe aquí.", video="lemma")
    add_text(tmp_path, "casa y casas y casas.", video="both")
    build_index(data_dir=tmp_path)
    response = Corpus(tmp_path).search("casas", source_language="es", limit=10)
    assert response["total_occurrences"] == 7
    assert response["returned"] == 5
    assert [r["match_type"] for r in response["results"]] == ["exact"] * 4 + ["lemma"]
    both = next(r for r in response["results"] if r["video"]["id"] == "both")
    assert both["matched_surface"] == "casas"


def test_versions_determinism_and_existing_analyzer_selection(tmp_path, monkeypatch):
    add_text(tmp_path, "Las casas son bonitas.")
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    before = corpus.search("casa", source_language="es")
    derived = (tmp_path / "derived/corpora/es/segments.jsonl").read_bytes()
    build_index(data_dir=tmp_path)
    assert corpus.search("casa", source_language="es") == before
    assert (tmp_path / "derived/corpora/es/segments.jsonl").read_bytes() == derived
    with sqlite3.connect(corpus.database) as connection:
        record = json.loads(
            connection.execute("SELECT provenance_json FROM analyzers").fetchone()[0]
        )
        record["identity"] = "incompatible"
        connection.execute("UPDATE analyzers SET provenance_json = ?", (json.dumps(record),))
    with pytest.raises(IncompatibleIndexError, match="rebuild"):
        corpus.search("casa", source_language="es")
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert (
            client.get("/api/v1/search", params={"language": "es", "q": "casa"}).status_code == 503
        )
    build_index(data_dir=tmp_path, analyzer="unicode")
    assert corpus.search("casa", source_language="es")["morphology_available"] is False
    assert corpus.search("casas", source_language="es")["total_occurrences"] == 1


def test_schema_uses_token_positions_without_materialized_ngram_occurrences(tmp_path):
    add_text(tmp_path, "Las casas son bonitas.")
    report = build_index(data_dir=tmp_path)
    with sqlite3.connect(tmp_path / "index/corpus.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        stream_count = connection.execute("SELECT COUNT(*) FROM token_streams").fetchone()[0]
        token_count = connection.execute("SELECT COUNT(*) FROM stream_tokens").fetchone()[0]
        recorded_occurrences = connection.execute(
            "SELECT occurrence_count FROM language_stats WHERE source_language = 'es'"
        ).fetchone()[0]

    assert {"token_streams", "stream_tokens", "language_stats"} <= tables
    assert "occurrences" not in tables
    assert "occurrence_keys" not in tables
    assert stream_count > 0
    assert token_count > 0
    assert recorded_occurrences == report["languages"]["es"]["occurrences"]


def test_analyzer_initialized_once_and_segments_analyzed_once(tmp_path, monkeypatch):
    initialized, analyzed = [], []

    class CountedAnalyzer(SimplemmaAnalyzer):
        def __init__(self, language):
            initialized.append(language)
            super().__init__(language)

        def analyze(self, text):
            analyzed.append(text)
            return super().analyze(text)

    monkeypatch.setattr(analyzers, "SimplemmaAnalyzer", CountedAnalyzer)
    add_text(tmp_path, "Casas bonitas.", repeats=3)
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    corpus.search("casa", source_language="es")
    corpus.search("casa", source_language="es")
    assert initialized == ["es"]
    assert analyzed.count("Casas bonitas.") == 3
    assert analyzed.count("casa") == 2


def test_language_resources_filter_only_suggestions(tmp_path):
    add_text(tmp_path, "the cats and the dogs.", language="en")
    add_text(tmp_path, "the cats and the dogs.", language="es")
    build_index(data_dir=tmp_path)
    corpus = Corpus(tmp_path)
    assert corpus.search("the", source_language="en")["total_occurrences"] == 2
    assert not any(r["text"] == "the" for r in corpus.suggestions(source_language="en"))
    assert any(r["text"] == "the" for r in corpus.suggestions(source_language="es", limit=30))


def test_unicode_offsets_and_invalid_spans():
    analyzer = UnicodeAnalyzer("es")
    text = "🙂 ¡Sí, l’amigo!"
    result = analyzer.analyze(text)
    assert [t.surface for t in result.tokens] == ["Sí", "l’amigo"]
    assert [t.normalized for t in result.tokens] == ["si", "l'amigo"]
    for token in result.tokens:
        assert text[token.start : token.end] == token.surface
    for token in [AnalyzedToken("wrong", "wrong", 0, 2), AnalyzedToken("Sí", "si", -1, 2)]:
        with pytest.raises(InvalidAnalysisError):
            Analysis((token,), analyzer.provenance).validate(text)


def test_primary_language_analysis_preserves_full_corpus_language(tmp_path):
    add_text(tmp_path, "casas.", language="pt-BR")
    add_text(tmp_path, "casas.", language="pt-PT")
    build_index(data_dir=tmp_path)
    result = Corpus(tmp_path).search("casa", source_language="pt-BR")
    assert result["total_occurrences"] == 1
    assert result["results"][0]["analyzer"]["language"] == "pt-BR"
    assert result["results"][0]["source_language"] == "pt-BR"


def test_failed_analysis_preserves_previous_index_and_derived_cache(tmp_path, monkeypatch):
    add_text(tmp_path, "Casas bonitas.")
    build_index(data_dir=tmp_path)
    database = (tmp_path / "index/corpus.sqlite3").read_bytes()
    derived = (tmp_path / "derived/corpora/es/segments.jsonl").read_bytes()
    analyzer = SimplemmaAnalyzer("es")

    def invalid(text):
        return Analysis((AnalyzedToken("bad", "bad", -1, 2),), analyzer.provenance)

    monkeypatch.setattr(analyzer, "analyze", invalid)
    monkeypatch.setattr(indexing, "get_analyzer", lambda *_: analyzer)
    with pytest.raises(InvalidAnalysisError):
        build_index(data_dir=tmp_path)
    assert (tmp_path / "index/corpus.sqlite3").read_bytes() == database
    assert (tmp_path / "derived/corpora/es/segments.jsonl").read_bytes() == derived


def test_accents_choose_strongest_occurrence_in_one_sentence(tmp_path):
    add_text(tmp_path, "Si quieres, sí podemos ir.")
    build_index(data_dir=tmp_path)
    result = Corpus(tmp_path).search("sí", source_language="es", match_mode="exact")
    assert result["total_occurrences"] == 2
    assert result["returned"] == 1
    assert result["results"][0]["match"]["accent_exact"] is True
