import json
from pathlib import Path

import pytest

from speech_retrieval import cli


def test_top_level_help_lists_provisional_commands(capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "download-subtitles" in output
    assert "build-index" in output
    assert "serve" in output
    assert "smoke" in output


def test_download_subtitles_dispatch_preserves_defaults(monkeypatch, capsys):
    received = {}

    def fake_acquire(**kwargs):
        received.update(kwargs)
        return {"complete": True, "successful": 10, "requested": 10}

    monkeypatch.setattr(cli, "acquire", fake_acquire)
    assert cli.main(["download-subtitles"]) == 0
    assert received == {
        "config_path": Path("config/channels/es.json"),
        "data_dir": Path("data"),
        "limit": 10,
        "scan_limit": 25,
    }
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_download_subtitles_preserves_incomplete_run_error(monkeypatch):
    monkeypatch.setattr(
        cli,
        "acquire",
        lambda **_kwargs: {"complete": False, "successful": 1, "requested": 2},
    )
    with pytest.raises(SystemExit, match="Only acquired 1 of 2 transcripts"):
        cli.main(["download-subtitles"])


def test_build_index_dispatch_preserves_defaults(monkeypatch, capsys):
    received = {}

    def fake_build_index(**kwargs):
        received.update(kwargs)
        return {"video_count": 1}

    monkeypatch.setattr(cli, "build_index", fake_build_index)
    assert cli.main(["build-index"]) == 0
    assert received == {
        "data_dir": Path("data"),
        "max_ngram": 5,
        "analyzer": "auto",
        "models_dir": None,
    }
    assert json.loads(capsys.readouterr().out) == {"video_count": 1}


def test_serve_preserves_missing_frontend_error(tmp_path):
    with pytest.raises(SystemExit, match="Frontend build missing"):
        cli.main(["serve", "--web-dist", str(tmp_path / "missing")])


def test_serve_dispatch_preserves_defaults(tmp_path, monkeypatch):
    web_dist = tmp_path / "web" / "dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text("<!doctype html>")
    app = object()
    received = {}

    def fake_create_app(**kwargs):
        received["create_app"] = kwargs
        return app

    def fake_run(received_app, **kwargs):
        received["run"] = {"app": received_app, **kwargs}

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    assert cli.main(["serve", "--web-dist", str(web_dist)]) == 0
    assert received == {
        "create_app": {
            "data_dir": Path("data"),
            "catalogue_dir": Path("config/channels"),
            "web_dist": web_dist,
            "models_dir": None,
        },
        "run": {"app": app, "host": "127.0.0.1", "port": 8000},
    }


def test_smoke_builds_and_queries_temporary_synthetic_corpus(capsys):
    assert cli.main(["smoke"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "ready": True,
        "videos": 1,
        "segments": 1,
        "query": "real example",
        "matches": 1,
    }


def test_models_download_dispatch_uses_explicit_directory(tmp_path, monkeypatch, capsys):
    received: dict[str, str | Path] = {}

    def fake_download(language, models_dir):
        received.update(language=language, models_dir=models_dir)
        return {"models_dir": str(models_dir), "analyzer": {"name": "stanza"}}

    monkeypatch.setattr(cli, "download_models", fake_download)
    assert cli.main(["models", "download", "ja", "--data-dir", str(tmp_path)]) == 0
    assert received == {"language": "ja", "models_dir": tmp_path / "models" / "stanza"}
    assert json.loads(capsys.readouterr().out)["analyzer"]["name"] == "stanza"
    override = tmp_path / "custom"
    assert cli.main(["models", "download", "zh-Hant", "--models-dir", str(override)]) == 0
    assert received == {"language": "zh-Hant", "models_dir": override}


def test_build_index_passes_analyzer_and_model_directory(tmp_path, monkeypatch):
    received = {}

    def fake_build(**kwargs):
        received.update(kwargs)
        return {}

    monkeypatch.setattr(cli, "build_index", fake_build)
    cli.main(["build-index", "--analyzer", "stanza", "--models-dir", str(tmp_path)])
    assert received["analyzer"] == "stanza"
    assert received["models_dir"] == tmp_path
