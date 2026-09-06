import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from speech_retrieval import cli
from speech_retrieval.contracts import UpdateSummary


def test_top_level_help_lists_stable_commands_and_removes_provisional_commands(capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for command in (
        "serve",
        "update",
        "search",
        "channels",
        "status",
        "reindex",
        "models",
        "doctor",
    ):
        assert command in output
    assert "download-subtitles" not in output
    assert "build-index" not in output


def test_serve_is_api_only_by_default_and_uses_environment(monkeypatch, tmp_path):
    received = {}
    app = object()
    monkeypatch.setenv("SPEECH_RETRIEVAL_DATA_DIR", str(tmp_path))

    def fake_create_app(settings):
        received["settings"] = settings
        return app

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda value, **kwargs: received.update(app=value, run=kwargs)
    )
    assert cli.main(["serve", "--port", "9123"]) == 0
    assert received["settings"].data_dir == tmp_path
    assert received["settings"].web_dist is None
    assert received["run"] == {"host": "127.0.0.1", "port": 9123}


def test_serve_rejects_missing_explicit_frontend(tmp_path, capsys):
    assert cli.main(["serve", "--web-dist", str(tmp_path / "missing")]) == 1
    assert "frontend build not found" in capsys.readouterr().err


def test_update_requires_once_and_returns_summary_exit_code(monkeypatch, capsys):
    assert cli.main(["update", "--json"]) == 1
    assert "requires --once" in capsys.readouterr().err
    summary = UpdateSummary(
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:01Z",
        successful=True,
        downloaded=0,
        cached=1,
        failures=0,
        languages=[],
        index={},
    )
    monkeypatch.setattr(
        cli, "Indexer", lambda _settings: SimpleNamespace(update_once=lambda: summary)
    )
    assert cli.main(["update", "--once", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cached"] == 1


def test_models_download_and_list_use_resolved_directory(tmp_path, monkeypatch, capsys):
    received: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "download_models",
        lambda language, path: received.update(language=language, path=path) or {"ok": True},
    )
    assert cli.main(["models", "download", "ja", "--data-dir", str(tmp_path), "--json"]) == 0
    assert received == {"language": "ja", "path": (tmp_path / "models" / "stanza").resolve()}
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    monkeypatch.setattr(
        cli, "list_models", lambda path: [{"language": "ja", "installed": True, "processors": []}]
    )
    assert cli.main(["models", "list", "--data-dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["language"] == "ja"


def test_repository_compatibility_entry_points_keep_script_defaults(monkeypatch, capsys):
    received = {}
    monkeypatch.setattr(
        cli,
        "acquire",
        lambda **kwargs: received.update(kwargs) or {"complete": True},
    )
    assert cli.download_subtitles_main([]) == 0
    assert received == {
        "config_path": Path("config/channels/es.json"),
        "data_dir": Path("data"),
        "limit": 10,
        "scan_limit": 25,
    }
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_smoke_builds_and_queries_temporary_synthetic_corpus(capsys):
    assert cli.main(["smoke", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "ready": True,
        "videos": 1,
        "segments": 1,
        "query": "real example",
        "matches": 1,
    }
