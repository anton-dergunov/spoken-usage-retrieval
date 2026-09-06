import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from speech_retrieval.api import create_app
from speech_retrieval.identity import CACHE_SCHEMA_VERSION, track_id, video_key
from speech_retrieval.indexing import build_index
from speech_retrieval.search import Corpus, IncompatibleIndexError
from speech_retrieval.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def write_catalogue(catalogue_dir: Path, language: str, *, enabled: bool = True) -> None:
    catalogue_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "language": language,
        "sections": [
            {
                "id": "fixtures",
                "name": "Fixtures",
                "channels": [
                    {
                        "id": f"channel-{language.casefold()}",
                        "name": f"Channel {language}",
                        "url": f"https://example.test/{language}",
                        "enabled": enabled,
                    }
                ],
            }
        ],
    }
    (catalogue_dir / f"{language}.json").write_text(json.dumps(payload))


def add_video(
    data_dir: Path,
    video_id: str,
    channel: str,
    *,
    language: str = "es",
    payload_name: str = "manual.json3",
) -> None:
    provider = "youtube"
    stable_video_key = video_key(provider, language, video_id)
    stable_track_id = track_id(stable_video_key, "manual", language)
    video_dir = data_dir / "raw" / "corpora" / language / stable_video_key / stable_track_id
    video_dir.mkdir(parents=True)
    caption_bytes = (FIXTURES / payload_name).read_bytes()
    metadata = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "catalogue_schema_version": 1,
        "catalogue_id": language,
        "source_language": language,
        "video_key": stable_video_key,
        "track_id": stable_track_id,
        "video_id": video_id,
        "provider": provider,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": f"Video {video_id}",
        "channel_id": f"channel-{channel}",
        "channel": channel,
        "channel_config_id": channel.casefold().replace(" ", "-"),
        "duration": 6,
        "upload_date": "20260901",
        "thumbnail": None,
        "varieties": ["Fixture"],
        "speech_style": ["conversation"],
        "caption_kind": "manual",
        "caption_language": language,
        "content_sha256": hashlib.sha256(caption_bytes).hexdigest(),
    }
    (video_dir / "metadata.json").write_text(json.dumps(metadata))
    (video_dir / "subtitles.raw.json3").write_bytes(caption_bytes)


def indexed_data(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    catalogue_dir = tmp_path / "config" / "channels"
    write_catalogue(catalogue_dir, "es")
    add_video(data_dir, "video-one", "Channel One")
    add_video(data_dir, "video-two", "Channel Two")
    build_index(data_dir=data_dir, max_ngram=5)
    return data_dir, catalogue_dir


def test_index_supports_accent_tolerant_words_phrases_and_highlights(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    corpus = Corpus(data_dir, catalogue_dir)
    accented = corpus.search("si", source_language="es", match_mode="exact")
    assert accented["source_language"] == "es"
    assert accented["results"][0]["match"]["text"] == "Sí"
    assert accented["results"][0]["match"]["accent_exact"] is False
    exact = corpus.search("Sí", source_language="es", match_mode="exact")
    assert exact["results"][0]["match"]["accent_exact"] is True
    phrase = corpus.search("la verdad", source_language="es", match_mode="exact")
    assert phrase["total_occurrences"] == 4
    assert phrase["returned"] == 4
    assert {result["video"]["id"] for result in phrase["results"][:2]} == {
        "video-one",
        "video-two",
    }
    result = phrase["results"][0]
    assert result["source_language"] == "es"
    assert result["video"]["source_language"] == "es"
    assert result["video"]["caption_language"] == "es"
    assert (
        result["sentence"][result["match"]["char_start"] : result["match"]["char_end"]].casefold()
        == "la verdad"
    )
    assert result["segments"] == [
        {
            "text": result["sentence"],
            "start": result["sentence_start"],
            "end": result["sentence_end"],
            "char_start": 0,
            "char_end": len(result["sentence"]),
        }
    ]


def test_search_suggestions_counts_and_status_are_language_scoped(tmp_path):
    data_dir = tmp_path / "data"
    catalogue_dir = tmp_path / "config" / "channels"
    write_catalogue(catalogue_dir, "es")
    write_catalogue(catalogue_dir, "en", enabled=False)
    add_video(data_dir, "shared-video", "Spanish", language="es")
    add_video(data_dir, "shared-video", "English", language="en")
    report = build_index(data_dir=data_dir, max_ngram=5)
    corpus = Corpus(data_dir, catalogue_dir)

    spanish = corpus.search("la verdad", source_language="es", match_mode="exact")
    english = corpus.search("la verdad", source_language="en")
    assert spanish["total_occurrences"] == 2
    assert english["total_occurrences"] == 2
    assert spanish["results"][0]["segment_id"] != english["results"][0]["segment_id"]
    assert (
        spanish["results"][0]["video"]["video_key"] != english["results"][0]["video"]["video_key"]
    )
    assert all(item["source_language"] == "en" for item in corpus.suggestions(source_language="en"))
    assert report["languages"]["es"]["videos"] == 1
    assert report["languages"]["en"]["videos"] == 1

    status = corpus.status()
    assert status["configured_languages"] == ["en", "es"]
    assert status["enabled_languages"] == ["es"]
    assert status["indexed_languages"] == ["en", "es"]
    assert status["videos"] == 2
    assert {item["source_language"]: item["videos"] for item in status["languages"]} == {
        "en": 1,
        "es": 1,
    }
    assert (
        next(item for item in status["languages"] if item["source_language"] == "en")["enabled"]
        is False
    )


def test_suggestions_use_no_hidden_language_specific_stopwords(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    suggestions = Corpus(data_dir, catalogue_dir).suggestions(source_language="es", limit=12)
    assert suggestions
    assert all(item["source_language"] == "es" for item in suggestions)
    assert any(item["size"] > 1 for item in suggestions)


def test_api_contract_requires_and_preserves_language(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    app = create_app(Settings(data_dir=data_dir, catalogue_dir=catalogue_dir))
    with TestClient(app) as client:
        assert client.get("/api/v1/search", params={"q": "la verdad"}).status_code == 422
        response = client.get("/api/v1/search", params={"q": "la verdad", "language": "es"})
        assert response.status_code == 200
        assert response.json()["source_language"] == "es"
        assert response.json()["results"][0]["video"]["provider"] == "youtube"
        assert (
            client.get(
                "/api/v1/search",
                params={"q": "uno dos tres cuatro cinco seis", "language": "es"},
            ).status_code
            == 400
        )
        assert (
            client.get("/api/v1/search", params={"q": "inexistente", "language": "es"}).json()[
                "results"
            ]
            == []
        )
        assert (
            client.get("/api/v1/search", params={"q": "test", "language": "pt-BR"}).status_code
            == 400
        )
        assert (
            client.get("/api/v1/suggestions", params={"language": "not_a_tag"}).status_code == 400
        )
        suggestion_body = client.get("/api/v1/suggestions", params={"language": "es"}).json()
        assert suggestion_body["source_language"] == "es"
        status = client.get("/api/v1/status").json()
    assert status["videos"] == 2
    assert status["max_ngram"] == 5
    assert status["analyzer_selection"] == "auto"
    assert status["database_schema_version"] == 3


def test_pre_version_index_requires_a_rebuild(tmp_path):
    database = tmp_path / "data" / "index" / "corpus.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('version', '0.1.0')")
    with pytest.raises(IncompatibleIndexError, match="rebuild"):
        Corpus(tmp_path / "data").status()
