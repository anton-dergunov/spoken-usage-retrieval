import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_index_search_api import indexed_data, write_catalogue

from speech_retrieval import (
    ChannelRepository,
    Corpus,
    SearchResponse,
    Settings,
    create_app,
)
from speech_retrieval.channels import ChannelConflictError, ChannelNotFoundError
from speech_retrieval.contracts import ChannelCreate, ChannelUpdate


def test_settings_load_environment_and_are_frozen(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEECH_RETRIEVAL_DATA_DIR", str(tmp_path / "corpus"))
    monkeypatch.setenv("SPEECH_RETRIEVAL_ENABLE_CHANNEL_MUTATIONS", "true")
    monkeypatch.setenv("SPEECH_RETRIEVAL_CORS_ORIGINS", "https://one.test,https://two.test")
    monkeypatch.setenv("GEMINI_API_KEY", "private-test-key")
    monkeypatch.setenv("SPEECH_RETRIEVAL_TRANSLATION_TARGET_LANGUAGES", "en,ru,pt-BR")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path / "corpus"
    assert settings.enable_channel_mutations is True
    assert settings.cors_origins == ("https://one.test", "https://two.test")
    assert settings.translation_target_languages == ("en", "ru", "pt-BR")
    assert settings.gemini_api_key == "private-test-key"
    assert "private-test-key" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.port = 9000  # type: ignore[misc]


def test_corpus_context_typed_clip_statistics_and_seeded_random(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    with Corpus(settings) as corpus:
        first = corpus.search("la verdad", source_language="es", order="random", seed=10, limit=4)
        repeated = corpus.search(
            "la verdad", source_language="es", order="random", seed=10, limit=4
        )
        changed = corpus.search("la verdad", source_language="es", order="random", seed=11, limit=4)
        assert isinstance(first, SearchResponse)
        assert [item.occurrence_id for item in first.results] == [
            item.occurrence_id for item in repeated.results
        ]
        assert [item.occurrence_id for item in first.results] != [
            item.occurrence_id for item in changed.results
        ]
        assert [item.rank for item in first.results] == [1, 2, 3, 4]
        clip = corpus.clip(first.results[0].segment_id)
        assert clip.source_text == first.results[0].sentence
        assert clip.target_text is None
        assert clip.alignment_status == "unavailable"
        statistics = corpus.statistics()
        assert statistics.videos == 2
        assert statistics.segments > 0
        assert {item.channel_id for item in statistics.channels} >= {
            "channel-es",
            "channel-one",
            "channel-two",
        }
    with pytest.raises(RuntimeError, match="closed"):
        corpus.status()


def test_ranked_order_rejects_seed_and_limits_are_not_silently_clamped(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    corpus = Corpus(Settings(data_dir=data_dir, catalogue_dir=catalogue_dir))
    with pytest.raises(ValueError, match="seed is only valid"):
        corpus.search("verdad", source_language="es", seed=1)
    with pytest.raises(ValueError, match="limit must be between"):
        corpus.search("verdad", source_language="es", limit=51)


def test_channel_repository_add_update_activation_conflicts_and_no_purge(tmp_path):
    catalogue_dir = tmp_path / "channels"
    write_catalogue(catalogue_dir, "es")
    raw_marker = tmp_path / "data" / "raw" / "kept.txt"
    raw_marker.parent.mkdir(parents=True)
    raw_marker.write_text("keep", encoding="utf-8")
    repository = ChannelRepository(catalogue_dir)
    created = repository.add(
        ChannelCreate(
            source_language="es",
            section_id="fixtures",
            id="second-channel",
            name="Second",
            url="https://example.test/second",
        )
    )
    assert created.enabled is False
    updated = repository.update(
        "es", "second-channel", ChannelUpdate(name="Renamed", description="Notes")
    )
    assert updated.name == "Renamed"
    assert repository.enable("es", "second-channel").enabled is True
    assert repository.enable("es", "second-channel").enabled is True
    assert repository.disable("es", "second-channel").enabled is False
    assert raw_marker.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ChannelConflictError):
        repository.add(
            ChannelCreate(
                source_language="es",
                section_id="fixtures",
                id="second-channel",
                name="Duplicate",
                url="https://example.test/duplicate",
            )
        )
    with pytest.raises(ChannelNotFoundError):
        repository.add(
            ChannelCreate(
                source_language="es",
                section_id="missing",
                id="third-channel",
                name="Third",
                url="https://example.test/third",
            )
        )


def test_versioned_api_health_errors_request_ids_clip_and_management(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 404
        live = client.get("/api/v1/health/live", headers={"X-Request-ID": "caller-1"})
        assert live.status_code == 200
        assert live.headers["X-Request-ID"] == "caller-1"
        assert client.get("/api/v1/health/ready").status_code == 200
        invalid = client.get("/api/v1/search", params={"q": "truth"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"
        disabled = client.post(
            "/api/v1/channels/es/channel-es/disable", headers={"Authorization": "Bearer x"}
        )
        assert disabled.status_code == 404
        assert disabled.json()["error"]["code"] == "management_disabled"
        result = client.get("/api/v1/search", params={"q": "la verdad", "language": "es"}).json()
        clip = client.get(f"/api/v1/clips/{result['results'][0]['segment_id']}")
        assert clip.status_code == 200
        assert clip.json()["source_text"] == result["results"][0]["sentence"]
        missing = client.get("/api/v1/clips/not-a-segment")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "segment_not_found"
    with pytest.raises(RuntimeError, match="closed"):
        app.state.corpus.status()

    protected = Settings(
        data_dir=data_dir,
        catalogue_dir=catalogue_dir,
        enable_channel_mutations=True,
        operator_token="correct",
    )
    with TestClient(create_app(protected)) as client:
        route = "/api/v1/channels/es/channel-es/disable"
        assert client.post(route).status_code == 401
        assert client.post(route, headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.post(route, headers={"Authorization": "Bearer correct"})
        assert response.status_code == 200
        assert response.json()["enabled"] is False


def test_status_remains_readable_and_readiness_fails_without_index(tmp_path):
    catalogue_dir = tmp_path / "channels"
    write_catalogue(catalogue_dir, "es")
    settings = Settings(data_dir=tmp_path / "data", catalogue_dir=catalogue_dir)
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["ready"] is False
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "not_ready"


def test_management_payload_limit_and_non_loopback_security(tmp_path):
    with pytest.raises(ValueError, match="operator token"):
        create_app(Settings(host="0.0.0.0", enable_channel_mutations=True))
    settings = Settings(
        data_dir=tmp_path / "data",
        catalogue_dir=tmp_path / "channels",
        max_json_body_bytes=10,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/channels",
            content=json.dumps({"long": "payload"}),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


def test_checked_in_openapi_matches_application_contract():
    expected = json.loads(
        (Path(__file__).parents[1] / "docs" / "openapi-v1.json").read_text(encoding="utf-8")
    )
    assert create_app(Settings()).openapi() == expected
