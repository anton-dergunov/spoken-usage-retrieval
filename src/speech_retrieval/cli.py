from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from pydantic import BaseModel

from .acquisition import acquire
from .analysis import download_models, list_models
from .api import create_app
from .catalogue import load_catalogue_directory
from .channels import ChannelRepository
from .contracts import ChannelCreate, ChannelUpdate, DoctorCheck, DoctorReport
from .identity import CACHE_SCHEMA_VERSION, track_id, video_key
from .indexing import build_index
from .search import Corpus
from .service import Indexer, activity_is_alive, read_update_state
from .settings import Settings
from .translations import TranslationStore


def _payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_payload(item) for item in value]
    return value


def _emit(value: Any, json_output: bool = False) -> None:
    payload = _payload(value)
    if json_output or not isinstance(payload, list):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for item in payload:
        state = "enabled" if item.get("enabled") else "disabled"
        print(
            f"{item.get('source_language', '')}\t{item.get('id', '')}\t"
            f"{state}\t{item.get('name', '')}"
        )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--catalogue-dir", type=Path)
    parser.add_argument("--models-dir", type=Path)


def _settings(args: argparse.Namespace) -> Settings:
    base = Settings.from_env()
    names = (
        "data_dir",
        "catalogue_dir",
        "models_dir",
        "web_dist",
        "host",
        "port",
        "enable_channel_mutations",
        "acquisition_limit",
        "scan_limit",
        "max_ngram",
        "analyzer",
    )
    overrides = {
        name: getattr(args, name)
        for name in names
        if hasattr(args, name) and getattr(args, name) is not None
    }
    if getattr(args, "no_web", False):
        overrides["web_dist"] = None
    return base.with_overrides(**overrides)


def _serve(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if settings.web_dist is not None and not (settings.web_dist / "index.html").is_file():
        raise ValueError(f"frontend build not found: {settings.web_dist / 'index.html'}")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


def _update(args: argparse.Namespace) -> int:
    if not args.once:
        raise ValueError("update currently requires --once")
    summary = Indexer(_settings(args)).update_once()
    _emit(summary, args.json)
    return 0 if summary.successful else 1


def _search(args: argparse.Namespace) -> int:
    with Corpus(_settings(args)) as corpus:
        result = corpus.search(
            args.query,
            source_language=args.language,
            match_mode=args.match_mode,
            order=args.order,
            limit=args.limit,
            seed=args.seed,
        )
    if args.json:
        _emit(result, True)
    else:
        print(f"{result.total_occurrences} occurrences; showing {result.returned}")
        for item in result.results:
            print(f"{item.rank}. [{item.video.channel}] {item.sentence}")
    return 0


def _channels_list(args: argparse.Namespace) -> int:
    _emit(ChannelRepository(_settings(args).catalogue_dir).list(args.language), args.json)
    return 0


def _channels_add(args: argparse.Namespace) -> int:
    request = ChannelCreate(
        source_language=args.language,
        section_id=args.section,
        id=args.id,
        name=args.name,
        url=args.url,
        enabled=args.enabled,
        varieties=args.variety,
        speech_style=args.speech_style,
        description=args.description,
    )
    result = ChannelRepository(_settings(args).catalogue_dir).add(request)
    _emit(result, args.json)
    return 0


def _channels_update(args: argparse.Namespace) -> int:
    values = {
        name: getattr(args, name)
        for name in ("name", "url", "description", "varieties", "speech_style")
        if getattr(args, name) is not None
    }
    result = ChannelRepository(_settings(args).catalogue_dir).update(
        args.language, args.id, ChannelUpdate(**values)
    )
    _emit(result, args.json)
    return 0


def _channels_enabled(args: argparse.Namespace, enabled: bool) -> int:
    result = ChannelRepository(_settings(args).catalogue_dir).set_enabled(
        args.language, args.id, enabled
    )
    _emit(result, args.json)
    return 0


def _status(args: argparse.Namespace) -> int:
    with Corpus(_settings(args)) as corpus:
        result = corpus.status()
    _emit(result, args.json)
    return 0


def _reindex(args: argparse.Namespace) -> int:
    result = Indexer(_settings(args)).reindex()
    _emit(result, args.json)
    return 0


def _models_download(args: argparse.Namespace) -> int:
    settings = _settings(args)
    result = download_models(args.language, settings.resolved_models_dir)
    _emit(result, args.json)
    return 0


def _models_list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    models = list_models(settings.resolved_models_dir)
    if args.json:
        _emit(models, True)
    else:
        for model in models:
            state = "installed" if model["installed"] else "incomplete"
            processors = ", ".join(model["processors"]) or "no processors"
            print(f"{model['language']}\t{state}\t{processors}")
        if not models:
            print(f"No local Stanza models under {settings.resolved_models_dir}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    checks: list[DoctorCheck] = []
    try:
        catalogues = load_catalogue_directory(settings.catalogue_dir)
        checks.append(
            DoctorCheck(
                name="catalogues",
                status="ok" if catalogues else "warning",
                message=f"{len(catalogues)} valid catalogue(s)",
            )
        )
    except Exception as error:
        checks.append(DoctorCheck(name="catalogues", status="error", message=str(error)))
    try:
        with Corpus(settings) as corpus:
            corpus.check_ready()
            status = corpus.status()
        checks.append(
            DoctorCheck(
                name="index",
                status="ok",
                message=f"{status.videos} videos, schema {status.database_schema_version}",
            )
        )
    except Exception as error:
        checks.append(DoctorCheck(name="index", status="error", message=str(error)))
    models = list_models(settings.resolved_models_dir)
    checks.append(
        DoctorCheck(
            name="models",
            status="ok" if models else "warning",
            message=f"{len(models)} local Stanza model set(s)",
        )
    )
    ytdlp_available = importlib.util.find_spec("yt_dlp") is not None
    checks.append(
        DoctorCheck(
            name="yt_dlp",
            status="ok" if ytdlp_available else "error",
            message="yt-dlp is installed" if ytdlp_available else "yt-dlp is not installed",
        )
    )
    if settings.web_dist is not None:
        exists = (settings.web_dist / "index.html").is_file()
        checks.append(
            DoctorCheck(
                name="frontend",
                status="ok" if exists else "error",
                message=str(settings.web_dist),
            )
        )
    secure = not (
        settings.enable_channel_mutations
        and settings.host not in {"localhost", "127.0.0.1", "::1"}
        and not settings.operator_token
    )
    checks.append(
        DoctorCheck(
            name="management_security",
            status="ok" if secure else "error",
            message="channel mutation exposure is safe" if secure else "operator token required",
        )
    )
    state = read_update_state(settings.data_dir / "reports" / "update-state.json")
    current_activity = state.get("current_activity")
    if current_activity:
        active = activity_is_alive(current_activity)
        operation = (
            current_activity.get("operation", "unknown")
            if isinstance(current_activity, dict)
            else "unknown"
        )
        checks.append(
            DoctorCheck(
                name="activity",
                status="warning",
                message=("running" if active else "stale") + f" activity: {operation}",
            )
        )
    report = DoctorReport(healthy=not any(item.status == "error" for item in checks), checks=checks)
    _emit(report, args.json)
    return 0 if report.healthy else 1


def _translation_cache_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = TranslationStore(settings.data_dir / "derived" / "translations.sqlite3")
    _emit(store.statistics(settings.translation_concurrency), True)
    return 0


def _translation_cache_list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = TranslationStore(settings.data_dir / "derived" / "translations.sqlite3")
    _emit(
        store.entries(
            target_language=args.target_language,
            provider=args.provider,
            model=args.model,
            older_than_days=args.older_than_days,
            limit=args.limit,
        ),
        True,
    )
    return 0


def _translation_cache_prune(args: argparse.Namespace) -> int:
    if not args.all and not any(
        value is not None
        for value in (args.target_language, args.provider, args.model, args.older_than_days)
    ):
        raise ValueError("choose at least one cache filter or pass --all")
    if args.older_than_days is not None and args.older_than_days < 0:
        raise ValueError("older-than-days must not be negative")
    settings = _settings(args)
    store = TranslationStore(settings.data_dir / "derived" / "translations.sqlite3")
    removed = store.prune(
        target_language=args.target_language,
        provider=args.provider,
        model=args.model,
        older_than_days=args.older_than_days,
    )
    _emit({"removed": removed}, True)
    return 0


def _smoke(args: argparse.Namespace) -> int:
    _emit(run_smoke(), args.json)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speech-retrieval",
        description="Search, update, and serve a spoken-usage corpus.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the foreground HTTP service.")
    _common(serve)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--web-dist", type=Path)
    serve.add_argument("--no-web", action="store_true")
    serve.add_argument(
        "--enable-channel-mutations", action=argparse.BooleanOptionalAction, default=None
    )
    serve.set_defaults(handler=_serve)

    update = commands.add_parser("update", help="Acquire enabled channels and rebuild the index.")
    _common(update)
    update.add_argument("--once", action="store_true")
    update.add_argument("--limit", dest="acquisition_limit", type=int)
    update.add_argument("--scan-limit", type=int)
    update.add_argument("--max-ngram", type=int)
    update.add_argument("--analyzer", choices=("auto", "unicode", "simplemma", "stanza"))
    update.add_argument("--json", action="store_true")
    update.set_defaults(handler=_update)

    search = commands.add_parser("search", help="Search the corpus.")
    _common(search)
    search.add_argument("query")
    search.add_argument("--language", required=True)
    search.add_argument("--match-mode", choices=("auto", "exact", "lemma"), default="auto")
    search.add_argument("--order", choices=("ranked", "random"), default="ranked")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--seed", type=int)
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=_search)

    channel_group = commands.add_parser("channels", help="Manage channel catalogues.")
    channel_commands = channel_group.add_subparsers(dest="channel_command", required=True)
    channel_list = channel_commands.add_parser("list")
    _common(channel_list)
    channel_list.add_argument("--language")
    channel_list.add_argument("--json", action="store_true")
    channel_list.set_defaults(handler=_channels_list)

    channel_add = channel_commands.add_parser("add")
    _common(channel_add)
    channel_add.add_argument("--language", required=True)
    channel_add.add_argument("--section", required=True)
    channel_add.add_argument("--id", required=True)
    channel_add.add_argument("--name", required=True)
    channel_add.add_argument("--url", required=True)
    channel_add.add_argument("--enabled", action="store_true")
    channel_add.add_argument("--variety", action="append")
    channel_add.add_argument("--speech-style", action="append")
    channel_add.add_argument("--description")
    channel_add.add_argument("--json", action="store_true")
    channel_add.set_defaults(handler=_channels_add)

    channel_update = channel_commands.add_parser("update")
    _common(channel_update)
    channel_update.add_argument("--language", required=True)
    channel_update.add_argument("--id", required=True)
    channel_update.add_argument("--name")
    channel_update.add_argument("--url")
    channel_update.add_argument("--variety", dest="varieties", action="append")
    channel_update.add_argument("--speech-style", action="append")
    channel_update.add_argument("--description")
    channel_update.add_argument("--json", action="store_true")
    channel_update.set_defaults(handler=_channels_update)

    for name, enabled in (("enable", True), ("disable", False)):
        command = channel_commands.add_parser(name)
        _common(command)
        command.add_argument("--language", required=True)
        command.add_argument("--id", required=True)
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=lambda args, value=enabled: _channels_enabled(args, value))

    status = commands.add_parser("status", help="Show corpus readiness and counts.")
    _common(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=_status)

    reindex = commands.add_parser("reindex", help="Rebuild from cached transcripts.")
    _common(reindex)
    reindex.add_argument("--max-ngram", type=int)
    reindex.add_argument("--analyzer", choices=("auto", "unicode", "simplemma", "stanza"))
    reindex.add_argument("--json", action="store_true")
    reindex.set_defaults(handler=_reindex)

    models = commands.add_parser("models", help="Manage optional local Stanza models.")
    model_commands = models.add_subparsers(dest="model_command", required=True)
    model_download = model_commands.add_parser("download")
    _common(model_download)
    model_download.add_argument("language")
    model_download.add_argument("--json", action="store_true")
    model_download.set_defaults(handler=_models_download)
    model_list = model_commands.add_parser("list")
    _common(model_list)
    model_list.add_argument("--json", action="store_true")
    model_list.set_defaults(handler=_models_list)

    cache = commands.add_parser("translation-cache", help="Inspect or prune translation cache.")
    cache_commands = cache.add_subparsers(dest="translation_cache_command", required=True)
    cache_status = cache_commands.add_parser("status")
    _common(cache_status)
    cache_status.add_argument("--json", action="store_true")
    cache_status.set_defaults(handler=_translation_cache_status)
    cache_list = cache_commands.add_parser("list")
    _common(cache_list)
    cache_list.add_argument("--target-language")
    cache_list.add_argument("--provider")
    cache_list.add_argument("--model")
    cache_list.add_argument("--older-than-days", type=int)
    cache_list.add_argument("--limit", type=int, default=100)
    cache_list.add_argument("--json", action="store_true")
    cache_list.set_defaults(handler=_translation_cache_list)
    cache_prune = cache_commands.add_parser("prune")
    _common(cache_prune)
    cache_prune.add_argument("--target-language")
    cache_prune.add_argument("--provider")
    cache_prune.add_argument("--model")
    cache_prune.add_argument("--older-than-days", type=int)
    cache_prune.add_argument("--all", action="store_true")
    cache_prune.add_argument("--json", action="store_true")
    cache_prune.set_defaults(handler=_translation_cache_prune)

    doctor = commands.add_parser("doctor", help="Run offline configuration checks.")
    _common(doctor)
    doctor.add_argument("--web-dist", type=Path)
    doctor.add_argument("--no-web", action="store_true")
    doctor.add_argument("--host")
    doctor.add_argument(
        "--enable-channel-mutations", action=argparse.BooleanOptionalAction, default=None
    )
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_doctor)

    smoke = commands.add_parser("smoke", help="Run an offline synthetic service check.")
    smoke.add_argument("--json", action="store_true")
    smoke.set_defaults(handler=_smoke)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(dotenv_path=Path(".env"), override=False)
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = _parser().parse_args(arguments)
    try:
        return args.handler(args)
    except Exception as error:
        if "--json" in arguments:
            print(
                json.dumps(
                    {"error": {"code": "command_failed", "message": str(error)}},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1


# Compatibility entry points for repository scripts, not top-level CLI commands.
def download_subtitles_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--scan-limit", type=int, default=25)
    parser.add_argument("--channels", type=Path, default=Path("config/channels/es.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    report = acquire(
        config_path=args.channels,
        data_dir=args.data_dir,
        limit=args.limit,
        scan_limit=args.scan_limit,
    )
    _emit(report, True)
    return 0 if report["complete"] else 1


def build_index_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-ngram", type=int, default=5)
    parser.add_argument(
        "--analyzer", choices=("auto", "unicode", "simplemma", "stanza"), default="auto"
    )
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    _emit(
        build_index(
            data_dir=args.data_dir,
            max_ngram=args.max_ngram,
            analyzer=args.analyzer,
            models_dir=args.models_dir,
        ),
        True,
    )
    return 0


def serve_main(argv: Sequence[str] | None = None) -> int:
    return main(["serve", *(list(argv) if argv is not None else sys.argv[1:])])


def _synthetic_metadata(caption_bytes: bytes) -> dict[str, Any]:
    stable_video_key = video_key("youtube", "en", "synthetic-video")
    stable_track_id = track_id(stable_video_key, "manual", "en")
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "catalogue_schema_version": 1,
        "catalogue_id": "en",
        "source_language": "en",
        "video_key": stable_video_key,
        "track_id": stable_track_id,
        "video_id": "synthetic-video",
        "provider": "youtube",
        "url": "https://www.youtube.com/watch?v=synthetic-video",
        "title": "Synthetic smoke fixture",
        "channel_id": "synthetic-channel",
        "channel": "Synthetic channel",
        "channel_config_id": "synthetic",
        "duration": 4,
        "upload_date": "20260101",
        "thumbnail": None,
        "varieties": ["Synthetic"],
        "speech_style": ["conversation"],
        "caption_kind": "manual",
        "caption_language": "en",
        "content_sha256": hashlib.sha256(caption_bytes).hexdigest(),
    }


def run_smoke() -> dict[str, Any]:
    try:
        from fastapi.testclient import TestClient
    except ImportError as error:
        raise RuntimeError("The smoke check requires the test extra") from error
    captions = {
        "events": [
            {
                "tStartMs": 500,
                "dDurationMs": 2200,
                "segs": [{"utf8": "Yes, this is a real example."}],
            }
        ]
    }
    with tempfile.TemporaryDirectory(prefix="speech-retrieval-smoke-") as directory:
        root = Path(directory)
        data_dir = root / "data"
        catalogue_dir = root / "channels"
        catalogue_dir.mkdir()
        (catalogue_dir / "en.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "language": "en",
                    "sections": [
                        {
                            "id": "fixtures",
                            "name": "Fixtures",
                            "channels": [
                                {
                                    "id": "synthetic",
                                    "name": "Synthetic",
                                    "url": "https://example.test/synthetic",
                                    "enabled": True,
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        caption_bytes = json.dumps(captions).encode()
        metadata = _synthetic_metadata(caption_bytes)
        target = data_dir / "raw" / "corpora" / "en" / metadata["video_key"] / metadata["track_id"]
        target.mkdir(parents=True)
        (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (target / "subtitles.raw.json3").write_bytes(caption_bytes)
        build_index(data_dir=data_dir)
        settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
        with TestClient(create_app(settings)) as client:
            ready = client.get("/api/v1/health/ready")
            search = client.get("/api/v1/search", params={"q": "real example", "language": "en"})
            segment_id = search.json()["results"][0]["segment_id"]
            clip = client.get(f"/api/v1/clips/{segment_id}")
        if ready.status_code != 200 or search.status_code != 200 or clip.status_code != 200:
            raise RuntimeError("Synthetic service smoke failed")
        return {
            "ready": True,
            "videos": 1,
            "segments": 1,
            "query": search.json()["query"],
            "matches": search.json()["total_occurrences"],
        }
