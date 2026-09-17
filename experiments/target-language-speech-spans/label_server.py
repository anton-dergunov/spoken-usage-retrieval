"""A tiny local server for the language-labelling page that saves every label immediately.

Standard library only. It serves the same page as ``review.html``, streams each clip's audio with
HTTP range support so the player can seek, and writes the label store atomically after every
change, so a reload, a crash or a closed tab never loses work.

The label store is the durable, committed artifact. It carries the unit definitions, clip audio
checksums and label anchors alongside the labels, so other tasks in the repository can reuse it
without this run's gitignored intermediate files.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from language_review_app import LABEL_ANCHORS, render_language_review
from speech_spans import HUMAN_LABELS

STORE_SCHEMA_VERSION = 1
STORE_KIND = "spoken-language-unit-labels"


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_store(worksheet: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    """An empty label store for a frozen worksheet."""
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "kind": STORE_KIND,
        "run_id": worksheet["run_id"],
        "rubric_version": worksheet["rubric_version"],
        "items_checksum": worksheet["items_checksum"],
        "label_definitions": {value: anchor for value, _key, _label, anchor in LABEL_ANCHORS},
        "provenance": provenance,
        "clips": [{k: v for k, v in clip.items() if k != "audio"} for clip in worksheet["clips"]],
        "reviewer": None,
        "created_at": now(),
        "updated_at": now(),
        "items": [
            {
                "clip_id": item["clip_id"],
                "unit_id": item["unit_id"],
                "start": item["start"],
                "end": item["end"],
                "label": None,
                "note": None,
                "reviewer": None,
                "reviewed_at": None,
            }
            for item in worksheet["items"]
        ],
    }


def write_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def open_store(path: Path, worksheet: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    """Load the store for this worksheet, or create it. Refuse a store for different units."""
    if not path.is_file():
        store = new_store(worksheet, provenance)
        write_store(path, store)
        return store
    store = json.loads(path.read_text(encoding="utf-8"))
    if store.get("items_checksum") != worksheet["items_checksum"]:
        raise SystemExit(f"{path} belongs to different review units; refusing to overwrite it")
    return store


class LabelStore:
    """Thread-safe label mutations, each persisted before the request returns."""

    def __init__(self, path: Path, store: dict[str, Any]) -> None:
        self.path = path
        self.store = store
        self.index = {item["unit_id"]: item for item in store["items"]}
        self.lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "reviewer": self.store["reviewer"] or "",
                "labels": {i["unit_id"]: i["label"] for i in self.store["items"] if i["label"]},
                "times": {
                    i["unit_id"]: i["reviewed_at"] for i in self.store["items"] if i["label"]
                },
            }

    def set_reviewer(self, reviewer: str) -> None:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("reviewer name is empty")
        with self.lock:
            self.store["reviewer"] = reviewer
            self.store["updated_at"] = now()
            write_store(self.path, self.store)

    def set_label(self, unit_id: str, label: str | None) -> int:
        if label is not None and label not in HUMAN_LABELS:
            raise ValueError(f"unknown label {label!r}")
        with self.lock:
            item = self.index.get(unit_id)
            if item is None:
                raise KeyError(f"unknown unit {unit_id!r}")
            reviewer = self.store["reviewer"]
            if label is not None and not reviewer:
                raise PermissionError("enter a reviewer name before labelling")
            item["label"] = label
            item["reviewer"] = reviewer if label is not None else None
            item["reviewed_at"] = now() if label is not None else None
            self.store["updated_at"] = now()
            write_store(self.path, self.store)
            return sum(1 for entry in self.store["items"] if entry["label"])


_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def make_handler(
    page: str, audio: dict[str, Path], labels: LabelStore
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = page.encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, labels.state())
            elif self.path.startswith("/audio/") and self.path[7:] in audio:
                self._audio(audio[self.path[7:]])
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _audio(self, path: Path) -> None:
            size = path.stat().st_size
            start, end = 0, size - 1
            match = _RANGE.match(self.headers.get("Range", ""))
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                else:
                    start = max(0, size - int(match.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = stream.read(min(1 << 16, remaining))
                    if not block:
                        break
                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    remaining -= len(block)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            try:
                if self.path == "/api/label":
                    labelled = labels.set_label(body.get("unit_id", ""), body.get("label"))
                    self._json(HTTPStatus.OK, {"ok": True, "labelled": labelled})
                elif self.path == "/api/reviewer":
                    labels.set_reviewer(str(body.get("reviewer", "")))
                    self._json(HTTPStatus.OK, {"ok": True})
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except PermissionError as error:
                self._json(HTTPStatus.CONFLICT, {"error": str(error)})
            except (KeyError, ValueError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    return Handler


def build_server(
    worksheet: dict[str, Any],
    store_path: Path,
    provenance: dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[ThreadingHTTPServer, LabelStore]:
    labels = LabelStore(store_path, open_store(store_path, worksheet, provenance))
    audio = {
        clip["clip_id"]: Path(clip["audio"])
        for clip in worksheet["clips"]
        if Path(clip["audio"]).is_file()
    }
    page = render_language_review(worksheet, server=True)
    server = ThreadingHTTPServer((host, port), make_handler(page, audio, labels))
    return server, labels
