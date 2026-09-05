from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from .acquisition import acquire
from .api import create_app
from .identity import CACHE_SCHEMA_VERSION, track_id, video_key
from .indexing import build_index
from .search import Corpus


def _add_download_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit", type=int, default=10, help="Total successful transcripts to retain"
    )
    parser.add_argument(
        "--scan-limit", type=int, default=25, help="Maximum candidates examined per channel"
    )
    parser.add_argument("--channels", type=Path, default=Path("config/channels/es.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))


def _download_subtitles(args: argparse.Namespace) -> int:
    report = acquire(
        config_path=args.channels,
        data_dir=args.data_dir,
        limit=args.limit,
        scan_limit=args.scan_limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["complete"]:
        raise SystemExit(
            f"Only acquired {report['successful']} of {report['requested']} transcripts"
        )
    return 0


def download_subtitles_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cache source-language YouTube captions without downloading video."
    )
    _add_download_arguments(parser)
    return _download_subtitles(parser.parse_args(argv))


def _add_build_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-ngram", type=int, default=5)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))


def _build_index(args: argparse.Namespace) -> int:
    report = build_index(data_dir=args.data_dir, max_ngram=args.max_ngram)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_index_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the local exact word and phrase index.")
    _add_build_arguments(parser)
    return _build_index(parser.parse_args(argv))


def _add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--catalogue-dir", type=Path, default=Path("config/channels"))
    parser.add_argument("--web-dist", type=Path, default=Path("web/dist"))


def _serve(args: argparse.Namespace) -> int:
    if not (args.web_dist / "index.html").exists():
        raise SystemExit("Frontend build missing. Run: npm --prefix web run build")
    app = create_app(
        data_dir=args.data_dir,
        catalogue_dir=args.catalogue_dir,
        web_dist=args.web_dist,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def serve_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the speech retrieval API and web viewer.")
    _add_serve_arguments(parser)
    return _serve(parser.parse_args(argv))


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


def _synthetic_captions() -> dict[str, Any]:
    return {
        "events": [
            {
                "tStartMs": 500,
                "dDurationMs": 2200,
                "segs": [{"utf8": "Yes, this is a real example."}],
            }
        ]
    }


def run_smoke() -> dict[str, Any]:
    try:
        from fastapi.testclient import TestClient
    except ImportError as error:
        raise RuntimeError(
            "The smoke check requires the test extra: uv sync --extra test"
        ) from error

    with tempfile.TemporaryDirectory(prefix="speech-retrieval-smoke-") as directory:
        data_dir = Path(directory) / "data"
        caption_bytes = json.dumps(_synthetic_captions(), ensure_ascii=False).encode("utf-8")
        metadata = _synthetic_metadata(caption_bytes)
        video_dir = (
            data_dir / "raw" / "corpora" / "en" / metadata["video_key"] / metadata["track_id"]
        )
        video_dir.mkdir(parents=True)
        (video_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        (video_dir / "subtitles.raw.json3").write_bytes(caption_bytes)

        index_report = build_index(data_dir=data_dir, max_ngram=5)
        search_result = Corpus(data_dir).search("real example", source_language="en")
        if search_result["total_occurrences"] != 1 or not search_result["results"]:
            raise RuntimeError("Synthetic corpus search did not return the expected phrase")

        with TestClient(create_app(data_dir=data_dir, web_dist=None)) as client:
            status_response = client.get("/api/status")
            search_response = client.get(
                "/api/search", params={"q": "real example", "language": "en"}
            )
        if status_response.status_code != 200 or status_response.json().get("videos") != 1:
            raise RuntimeError("Synthetic corpus status endpoint failed")
        if search_response.status_code != 200 or not search_response.json().get("results"):
            raise RuntimeError("Synthetic corpus search endpoint failed")

        return {
            "ready": True,
            "videos": index_report["video_count"],
            "segments": index_report["segment_count"],
            "query": search_result["query"],
            "matches": search_result["total_occurrences"],
        }


def _smoke(_args: argparse.Namespace) -> int:
    print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speech-retrieval",
        description="Acquire, index, and serve the native-speech retrieval prototype.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser(
        "download-subtitles",
        help="Cache source-language YouTube captions without downloading video.",
    )
    _add_download_arguments(download)
    download.set_defaults(handler=_download_subtitles)

    build = subparsers.add_parser(
        "build-index", help="Build the local exact word and phrase index."
    )
    _add_build_arguments(build)
    build.set_defaults(handler=_build_index)

    serve = subparsers.add_parser("serve", help="Serve the API and built web viewer.")
    _add_serve_arguments(serve)
    serve.set_defaults(handler=_serve)

    smoke = subparsers.add_parser(
        "smoke", help="Run offline indexing and API checks with synthetic data."
    )
    smoke.set_defaults(handler=_smoke)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)
