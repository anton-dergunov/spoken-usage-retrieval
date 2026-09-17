"""Corpus operations over HTTP, and indexing that honours a disabled channel."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient
from test_index_search_api import add_video, indexed_data, write_catalogue

from speech_retrieval.api import create_app
from speech_retrieval.contracts import UpdateSummary
from speech_retrieval.indexing import build_index
from speech_retrieval.operations import CorpusOperations
from speech_retrieval.search import Corpus
from speech_retrieval.settings import Settings

TOKEN = "operator-token-for-tests"


class FakeIndexer:
    """Stands in for `Indexer`: records what ran, and can be held until a test releases it."""

    runs: list[str] = []
    gate: threading.Event | None = None
    fail: Exception | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _hold(self) -> None:
        if FakeIndexer.gate is not None:
            FakeIndexer.gate.wait(5)
        if FakeIndexer.fail is not None:
            raise FakeIndexer.fail

    def update_once(self) -> UpdateSummary:
        FakeIndexer.runs.append("update")
        self._hold()
        return UpdateSummary(
            started_at="2026-09-17T02:00:00+00:00",
            completed_at="2026-09-17T02:05:00+00:00",
            successful=True,
            downloaded=3,
            cached=10,
            failures=0,
            languages=[],
            index=None,
        )

    def reindex(self) -> dict:
        FakeIndexer.runs.append("reindex")
        self._hold()
        return {"video_count": 2}


@pytest.fixture(autouse=True)
def fresh_fake():
    FakeIndexer.runs, FakeIndexer.gate, FakeIndexer.fail = [], None, None
    yield
    if FakeIndexer.gate is not None:
        FakeIndexer.gate.set()


def settings_for(tmp_path, **overrides):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    return Settings(
        data_dir=data_dir,
        catalogue_dir=catalogue_dir,
        enable_channel_mutations=True,
        operator_token=TOKEN,
        **overrides,
    )


@pytest.fixture
def client(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        app.state.operations = CorpusOperations(settings, indexer=FakeIndexer)
        test_client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield test_client


def follow(client, operation_id):
    for _ in range(200):
        body = client.get(f"/api/v1/corpus/operations/{operation_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        threading.Event().wait(0.02)
    raise AssertionError("the operation never finished")


def test_an_update_is_started_and_followed_to_its_end(client):
    started = client.post("/api/v1/corpus/operations", json={"operation": "update"})
    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    finished = follow(client, started.json()["operation_id"])
    assert finished["status"] == "completed"
    assert finished["successful"] is True
    assert finished["summary"]["downloaded"] == 3
    assert FakeIndexer.runs == ["update"]


def test_a_reindex_reports_the_index_it_built(client):
    started = client.post("/api/v1/corpus/operations", json={"operation": "reindex"})
    finished = follow(client, started.json()["operation_id"])
    assert finished["index"] == {"video_count": 2}
    assert FakeIndexer.runs == ["reindex"]


def test_a_second_request_while_one_is_active_is_answered_with_it(client):
    FakeIndexer.gate = threading.Event()
    first = client.post("/api/v1/corpus/operations", json={"operation": "update"})
    second = client.post("/api/v1/corpus/operations", json={"operation": "reindex"})
    assert second.status_code == 200
    assert second.json()["operation_id"] == first.json()["operation_id"]
    FakeIndexer.gate.set()
    follow(client, first.json()["operation_id"])
    assert FakeIndexer.runs == ["update"]
    listed = client.get("/api/v1/corpus/operations").json()
    assert [item["operation_id"] for item in listed] == [first.json()["operation_id"]]


def test_a_failure_is_recorded_on_the_operation(client):
    FakeIndexer.fail = RuntimeError("another corpus operation is already running")
    started = client.post("/api/v1/corpus/operations", json={})
    finished = follow(client, started.json()["operation_id"])
    assert finished["status"] == "failed"
    assert finished["successful"] is False
    assert "already running" in finished["error"]


def test_operations_need_the_operator_token(client):
    client.headers.pop("Authorization")
    assert client.post("/api/v1/corpus/operations", json={}).status_code == 401
    assert client.get("/api/v1/corpus/operations").status_code == 401
    assert FakeIndexer.runs == []


def test_operations_are_off_where_management_is(tmp_path):
    app = create_app(
        settings_for(tmp_path).with_overrides(enable_channel_mutations=False, operator_token=None)
    )
    with TestClient(app) as test_client:
        assert test_client.post("/api/v1/corpus/operations", json={}).status_code == 404


def test_an_unknown_operation_is_not_found_and_an_unknown_kind_refused(client):
    assert client.get("/api/v1/corpus/operations/nope").status_code == 404
    assert client.post("/api/v1/corpus/operations", json={"operation": "delete"}).status_code == 422


def test_an_operation_active_when_the_process_stopped_is_recorded_as_interrupted(tmp_path):
    settings = settings_for(tmp_path)
    reports = settings.data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "corpus-operations.json").write_text(
        json.dumps(
            [
                {
                    "operation_id": "left-running",
                    "operation": "update",
                    "status": "running",
                    "created_at": "2026-09-17T02:00:00+00:00",
                    "started_at": "2026-09-17T02:00:01+00:00",
                }
            ]
        )
    )
    operations = CorpusOperations(settings, indexer=FakeIndexer)
    record = operations.get("left-running")
    assert record.status == "interrupted"
    assert operations.active() is None

    async def start():
        started, fresh = await operations.start("update")
        await operations.wait()
        return started, fresh

    started, fresh = asyncio.run(start())
    assert fresh is True
    assert operations.get(started.operation_id).status == "completed"


def test_the_history_is_bounded(tmp_path):
    settings = settings_for(tmp_path)
    operations = CorpusOperations(settings, indexer=FakeIndexer)

    async def run_many():
        for _ in range(55):
            await operations.start("reindex")
            await operations.wait()

    asyncio.run(run_many())
    assert len(operations.list(50)) == 50


# ── indexing honours `enabled` ──────────────────────────────────────────────


def catalogue_with(catalogue_dir, language, channels):
    catalogue_dir.mkdir(parents=True, exist_ok=True)
    (catalogue_dir / f"{language}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "language": language,
                "sections": [
                    {
                        "id": "fixtures",
                        "name": "Fixtures",
                        "channels": [
                            {
                                "id": identifier,
                                "name": identifier,
                                "url": f"https://example.test/{identifier}",
                                "enabled": enabled,
                            }
                            for identifier, enabled in channels
                        ],
                    }
                ],
            }
        )
    )


def test_a_disabled_channels_clips_leave_search_at_the_next_build(tmp_path):
    data_dir = tmp_path / "data"
    catalogue_dir = tmp_path / "channels"
    catalogue_with(catalogue_dir, "es", [("channel-one", True), ("channel-two", True)])
    add_video(data_dir, "video-one", "Channel One")
    add_video(data_dir, "video-two", "Channel Two")
    build_index(data_dir=data_dir, catalogue_dir=catalogue_dir)
    before = Corpus(data_dir, catalogue_dir).search(
        "la verdad", source_language="es", match_mode="exact"
    )
    assert {item["video"]["id"] for item in before["results"]} == {"video-one", "video-two"}

    catalogue_with(catalogue_dir, "es", [("channel-one", True), ("channel-two", False)])
    report = build_index(data_dir=data_dir, catalogue_dir=catalogue_dir)
    after = Corpus(data_dir, catalogue_dir).search(
        "la verdad", source_language="es", match_mode="exact"
    )
    assert {item["video"]["id"] for item in after["results"]} == {"video-one"}
    assert report["excluded_disabled_channels"] == {"es/channel-two": 1}
    assert report["video_count"] == 1
    # The captions stay cached: switching it back on needs no download.
    assert (data_dir / "raw" / "corpora" / "es").exists()
    catalogue_with(catalogue_dir, "es", [("channel-one", True), ("channel-two", True)])
    assert build_index(data_dir=data_dir, catalogue_dir=catalogue_dir)["video_count"] == 2


def test_a_channel_no_longer_listed_keeps_its_clips(tmp_path):
    data_dir = tmp_path / "data"
    catalogue_dir = tmp_path / "channels"
    catalogue_with(catalogue_dir, "es", [("channel-one", True)])
    add_video(data_dir, "video-one", "Channel One")
    add_video(data_dir, "video-two", "Channel Two")
    assert build_index(data_dir=data_dir, catalogue_dir=catalogue_dir)["video_count"] == 2


def test_without_a_catalogue_everything_cached_is_indexed(tmp_path):
    data_dir = tmp_path / "data"
    add_video(data_dir, "video-one", "Channel One")
    assert build_index(data_dir=data_dir)["excluded_disabled_channels"] == {}


def test_every_channel_disabled_is_refused_rather_than_an_empty_index(tmp_path):
    data_dir = tmp_path / "data"
    catalogue_dir = tmp_path / "channels"
    catalogue_with(catalogue_dir, "es", [("channel-one", False)])
    add_video(data_dir, "video-one", "Channel One")
    with pytest.raises(ValueError, match="disabled channel"):
        build_index(data_dir=data_dir, catalogue_dir=catalogue_dir)


def test_the_indexer_passes_its_catalogue_to_the_build(tmp_path, monkeypatch):
    from speech_retrieval import Indexer
    from speech_retrieval import service as service_module

    seen = {}
    monkeypatch.setattr(service_module, "build_index", lambda **kwargs: seen.update(kwargs) or {})
    settings = settings_for(tmp_path)
    write_catalogue(settings.catalogue_dir, "es")
    Indexer(settings).reindex()
    assert seen["catalogue_dir"] == settings.catalogue_dir
