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


def test_update_threads_the_audio_flag_through_settings_and_defaults_to_disabled(monkeypatch):
    monkeypatch.setenv("SPEECH_RETRIEVAL_WITH_AUDIO", "false")
    received = {}

    def fake_indexer(settings):
        received["with_audio"] = settings.with_audio
        return SimpleNamespace(
            update_once=lambda: UpdateSummary(
                started_at="2026-01-01T00:00:00Z",
                completed_at="2026-01-01T00:00:01Z",
                successful=True,
                downloaded=0,
                cached=0,
                failures=0,
                languages=[],
                index={},
            )
        )

    monkeypatch.setattr(cli, "Indexer", fake_indexer)
    assert cli.main(["update", "--once", "--json"]) == 0
    assert received["with_audio"] is False
    assert cli.main(["update", "--once", "--with-audio", "--json"]) == 0
    assert received["with_audio"] is True
    assert cli.main(["update", "--once", "--no-with-audio", "--json"]) == 0
    assert received["with_audio"] is False


def test_audio_cache_status_reports_totals_and_whether_audio_is_enabled(
    tmp_path, monkeypatch, capsys
):
    from audio_fixtures import install_caption_video, install_raw_audio, write_wave

    key = install_caption_video(tmp_path)
    install_raw_audio(
        tmp_path, key=key, source=write_wave(tmp_path / "s.wav", seconds=1.0), duration=1.0
    )
    monkeypatch.setenv("SPEECH_RETRIEVAL_WITH_AUDIO", "true")

    assert cli.main(["audio-cache", "status", "--data-dir", str(tmp_path), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["enabled"] is True
    assert (report["videos"], report["ready"], report["missing"]) == (1, 1, 0)
    assert report["raw_bytes"] > 0
    assert report["languages"][0]["language"] == "es"
    assert report["languages"][0]["channels"][0]["channel"] == "channel-a"


def test_audio_cache_prune_previews_by_default_and_protects_raw_audio(tmp_path, capsys):
    from audio_fixtures import (
        fake_conversion,
        fake_probe_runner,
        install_caption_video,
        install_raw_audio,
        write_wave,
    )

    from speech_retrieval.audio import audio_availability, prepare_clip

    key = install_caption_video(tmp_path)
    install_raw_audio(
        tmp_path, key=key, source=write_wave(tmp_path / "s.wav", seconds=2.0), duration=2.0
    )
    prepare_clip(
        tmp_path,
        language="es",
        video_key=key,
        start=0.0,
        end=1.0,
        conversion_runner=fake_conversion(),
        probe_runner=fake_probe_runner(1.0),
    )
    clip_root = tmp_path / "derived" / "audio" / "clips" / "es" / key

    assert cli.main(["audio-cache", "prune", "--data-dir", str(tmp_path), "--all"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["derived_clips"] == 1
    assert preview["raw_videos"] == 0
    assert preview["entries"][0]["deleted"] is False
    assert list(clip_root.iterdir())

    assert (
        cli.main(["audio-cache", "prune", "--data-dir", str(tmp_path), "--all", "--execute"]) == 0
    )
    executed = json.loads(capsys.readouterr().out)
    assert executed["dry_run"] is False
    assert executed["entries"][0]["deleted"] is True
    assert not clip_root.exists()
    assert audio_availability(tmp_path, language="es", video_key=key).ready


def test_audio_cache_prune_requires_a_selector(tmp_path, capsys):
    assert cli.main(["audio-cache", "prune", "--data-dir", str(tmp_path)]) == 1
    assert "at least one prune selector" in capsys.readouterr().err


def test_doctor_treats_media_tools_as_optional_until_audio_is_enabled(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setenv("SPEECH_RETRIEVAL_WITH_AUDIO", "false")

    assert cli.main(["doctor", "--data-dir", str(tmp_path), "--json"]) == 1
    disabled = {item["name"]: item for item in json.loads(capsys.readouterr().out)["checks"]}
    assert disabled["ffmpeg"]["status"] == "warning"
    assert disabled["ffprobe"]["status"] == "warning"
    assert "optional audio remains disabled" in disabled["ffmpeg"]["message"]

    monkeypatch.setenv("SPEECH_RETRIEVAL_WITH_AUDIO", "1")
    assert cli.main(["doctor", "--data-dir", str(tmp_path), "--json"]) == 1
    enabled = {item["name"]: item for item in json.loads(capsys.readouterr().out)["checks"]}
    assert enabled["ffmpeg"]["status"] == "error"
    assert "required while audio acquisition is enabled" in enabled["ffmpeg"]["message"]
