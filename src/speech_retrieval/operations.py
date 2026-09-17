"""Corpus operations started over HTTP: an update, or a rebuild of the index.

The same `Indexer` the `update --once` and `reindex` commands run, in a worker thread of the serving
process, so a host can keep its corpus fresh without a shell into the container. One operation at a
time: a second request while one is active is answered with the active one, because both callers —
a nightly timer and a person pressing Update now — want the corpus updated, not updated twice.

Operations are recorded in `reports/corpus-operations.json`, newest first and bounded. An operation
still marked active when the process starts ended with the process that ran it, and is recorded as
`interrupted`; nothing resumes. The index is swapped in atomically by `build_index`, so the corpus
keeps answering throughout.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from .contracts import CorpusOperation, CorpusOperationKind, UpdateSummary
from .service import Indexer
from .settings import Settings

HISTORY_LIMIT = 50
ACTIVE = ("queued", "running")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CorpusIndexer(Protocol):
    """What an operation runs: `Indexer`, or a stand-in for it."""

    def update_once(self) -> UpdateSummary: ...

    def reindex(self) -> dict[str, Any]: ...


class CorpusOperations:
    def __init__(
        self, settings: Settings, *, indexer: Callable[[Settings], CorpusIndexer] = Indexer
    ) -> None:
        self.settings = settings
        self.indexer = indexer
        self.path = settings.data_dir / "reports" / "corpus-operations.json"
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._recover()

    # ── the record ───────────────────────────────────────────────────────────

    def _read(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(records[:HISTORY_LIMIT], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _update(self, operation_id: str, **changes: Any) -> CorpusOperation:
        with self._lock:
            records = self._read()
            for record in records:
                if record["operation_id"] == operation_id:
                    record.update(changes)
                    self._write(records)
                    return CorpusOperation.model_validate(record)
        raise KeyError(operation_id)

    def _recover(self) -> None:
        with self._lock:
            records = self._read()
            changed = False
            for record in records:
                if record.get("status") in ACTIVE:
                    record.update(
                        status="interrupted",
                        error="The service stopped before this operation ended.",
                        completed_at=_now(),
                    )
                    changed = True
            if changed:
                self._write(records)

    # ── reading ──────────────────────────────────────────────────────────────

    def get(self, operation_id: str) -> CorpusOperation:
        for record in self._read():
            if record.get("operation_id") == operation_id:
                return CorpusOperation.model_validate(record)
        raise KeyError(operation_id)

    def list(self, limit: int = 20) -> list[CorpusOperation]:
        return [CorpusOperation.model_validate(item) for item in self._read()[:limit]]

    def active(self) -> CorpusOperation | None:
        return next((item for item in self.list(HISTORY_LIMIT) if item.status in ACTIVE), None)

    # ── starting ─────────────────────────────────────────────────────────────

    async def start(self, operation: CorpusOperationKind) -> tuple[CorpusOperation, bool]:
        """Start an operation, or return the active one. The flag says whether this started it."""
        with self._lock:
            current = next((item for item in self._read() if item.get("status") in ACTIVE), None)
            if current is not None:
                return CorpusOperation.model_validate(current), False
            record = CorpusOperation(
                operation_id=str(uuid.uuid4()),
                operation=operation,
                status="queued",
                created_at=_now(),
            )
            self._write([record.model_dump(mode="json"), *self._read()])
        self._task = asyncio.create_task(self._run(record.operation_id, operation))
        return record, True

    async def _run(self, operation_id: str, operation: CorpusOperationKind) -> None:
        self._update(operation_id, status="running", started_at=_now())
        indexer = self.indexer(self.settings)
        try:
            if operation == "reindex":
                report = await asyncio.to_thread(indexer.reindex)
                self._update(
                    operation_id,
                    status="completed",
                    successful=True,
                    index=report,
                    completed_at=_now(),
                )
            else:
                summary = await asyncio.to_thread(indexer.update_once)
                self._update(
                    operation_id,
                    status="completed",
                    successful=summary.successful,
                    summary=summary.model_dump(mode="json"),
                    completed_at=_now(),
                )
        except Exception as error:  # noqa: BLE001 - recorded on the operation, which is its report
            self._update(
                operation_id,
                status="failed",
                successful=False,
                error=str(error),
                completed_at=_now(),
            )

    async def wait(self) -> None:
        """For tests and shutdown: the operation this process started, to its end."""
        if self._task is not None:
            await asyncio.shield(self._task)

    async def aclose(self) -> None:
        # A thread cannot be interrupted. The operation keeps its lock until it ends; if the process
        # ends first, the next start records it as interrupted.
        self._task = None
