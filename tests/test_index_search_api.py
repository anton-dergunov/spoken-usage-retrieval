import json
from pathlib import Path

from fastapi.testclient import TestClient

from speech_retrieval.api import create_app
from speech_retrieval.indexing import build_index
from speech_retrieval.search import Corpus

FIXTURES = Path(__file__).parent / "fixtures"


def add_video(data_dir: Path, video_id: str, channel: str, payload_name: str = "manual.json3") -> None:
    video_dir = data_dir / "raw" / "videos" / video_id
    video_dir.mkdir(parents=True)
    metadata = {
        "video_id": video_id,
        "provider": "youtube",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": f"Video {video_id}",
        "channel_id": f"channel-{channel}",
        "channel": channel,
        "channel_config_id": channel.casefold(),
        "duration": 6,
        "upload_date": "20260901",
        "thumbnail": None,
        "varieties": ["Spain"],
        "speech_style": ["conversation"],
        "caption_kind": "manual",
        "caption_language": "es",
    }
    (video_dir / "metadata.json").write_text(json.dumps(metadata))
    (video_dir / "subtitles.raw.json3").write_text((FIXTURES / payload_name).read_text())


def indexed_data(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    add_video(data_dir, "video-one", "Channel One")
    add_video(data_dir, "video-two", "Channel Two")
    build_index(data_dir=data_dir, max_ngram=5)
    return data_dir


def test_index_supports_accent_tolerant_words_phrases_and_highlights(tmp_path):
    data_dir = indexed_data(tmp_path)
    corpus = Corpus(data_dir)
    accented = corpus.search("si")
    assert accented["results"][0]["match"]["text"] == "Sí"
    assert accented["results"][0]["match"]["accent_exact"] is False
    exact = corpus.search("Sí")
    assert exact["results"][0]["match"]["accent_exact"] is True
    phrase = corpus.search("la verdad")
    assert phrase["total_occurrences"] == 4
    assert phrase["returned"] == 4
    assert {result["video"]["id"] for result in phrase["results"][:2]} == {"video-one", "video-two"}
    assert phrase["results"][0]["sentence"][phrase["results"][0]["match"]["char_start"]:phrase["results"][0]["match"]["char_end"]].casefold() == "la verdad"
    assert phrase["results"][0]["segments"] == [{
        "text": phrase["results"][0]["sentence"],
        "start": phrase["results"][0]["sentence_start"],
        "end": phrase["results"][0]["sentence_end"],
        "char_start": 0,
        "char_end": len(phrase["results"][0]["sentence"]),
    }]


def test_suggestions_filter_stopwords_and_include_phrases(tmp_path):
    suggestions = Corpus(indexed_data(tmp_path)).suggestions(12)
    assert suggestions
    assert all(item["normalized"] not in {"la", "es", "una"} for item in suggestions)
    assert any(item["size"] > 1 for item in suggestions)


def test_api_contract_and_query_validation(tmp_path):
    data_dir = indexed_data(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, web_dist=None))
    response = client.get("/api/search", params={"q": "la verdad"})
    assert response.status_code == 200
    assert response.json()["results"][0]["video"]["provider"] == "youtube"
    assert client.get("/api/search", params={"q": "uno dos tres cuatro cinco seis"}).status_code == 400
    assert client.get("/api/search", params={"q": "inexistente"}).json()["results"] == []
    status = client.get("/api/status").json()
    assert status["videos"] == 2
    assert status["max_ngram"] == 5
