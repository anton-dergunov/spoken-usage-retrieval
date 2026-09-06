import json
import os

import pytest
from test_index_search_api import indexed_data, write_catalogue

from speech_retrieval import Indexer, Settings
from speech_retrieval import service as service_module


def test_indexer_rejects_a_concurrent_corpus_operation(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", catalogue_dir=tmp_path / "channels")
    indexer = Indexer(settings)
    indexer.lock_path.parent.mkdir(parents=True)
    indexer.lock_path.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already running"):
        indexer.update_once()


def test_update_once_covers_enabled_languages_and_records_success(tmp_path, monkeypatch):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    write_catalogue(catalogue_dir, "en")

    def fake_acquire(**kwargs):
        language = kwargs["config_path"].stem
        return {
            "completed_at": "2026-09-06T12:00:00+00:00",
            "videos": [{"status": "cached"}],
            "failures": [],
            "complete": True,
            "source_language": language,
        }

    monkeypatch.setattr(service_module, "acquire", fake_acquire)
    summary = Indexer(
        Settings(data_dir=data_dir, catalogue_dir=catalogue_dir, acquisition_limit=1)
    ).update_once()
    assert summary.successful is True
    assert summary.downloaded == 0
    assert summary.cached == 2
    assert [item.source_language for item in summary.languages] == ["en", "es"]
    state = json.loads((data_dir / "reports" / "update-state.json").read_text())
    assert state["current_activity"] is None
    assert state["last_successful_update"] == summary.completed_at


def test_update_once_continues_after_language_failure_and_preserves_last_success(
    tmp_path, monkeypatch
):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    write_catalogue(catalogue_dir, "en")
    state_path = data_dir / "reports" / "update-state.json"
    state_path.write_text(
        json.dumps(
            {
                "current_activity": None,
                "last_successful_update": "2026-09-05T12:00:00+00:00",
                "recent_failures": [],
            }
        )
    )

    def fake_acquire(**kwargs):
        if kwargs["config_path"].stem == "en":
            raise RuntimeError("provider unavailable")
        return {
            "completed_at": "2026-09-06T12:00:00+00:00",
            "videos": [{"status": "cached"}],
            "failures": [],
            "complete": True,
        }

    monkeypatch.setattr(service_module, "acquire", fake_acquire)
    summary = Indexer(
        Settings(data_dir=data_dir, catalogue_dir=catalogue_dir, acquisition_limit=1)
    ).update_once()
    assert summary.successful is False
    assert summary.cached == 1
    assert summary.failures == 1
    assert summary.index is not None
    state = json.loads(state_path.read_text())
    assert state["last_successful_update"] == "2026-09-05T12:00:00+00:00"
    assert state["recent_failures"][0]["source_language"] == "en"
