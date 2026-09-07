from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .catalogue import canonical_language
from .contracts import (
    Clip,
    TranslationBatch,
    TranslationBatchCounts,
    TranslationBatchItem,
    TranslationCacheStatistics,
    TranslationErrorInfo,
    TranslationJob,
    TranslationResult,
)

CACHE_SCHEMA_VERSION = 2


def now() -> str:
    return datetime.now(UTC).isoformat()


class TranslationStore:
    """SQLite persistence for stage caches, attempts, jobs, and batches."""

    def __init__(self, path: Path, *, recover_unfinished: bool = False):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS translation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO translation_meta VALUES ('schema_version', '2');
                CREATE TABLE IF NOT EXISTS translation_entries (
                    cache_key TEXT PRIMARY KEY,
                    source_text_hash TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    value_json TEXT,
                    error_json TEXT,
                    segment_id TEXT NOT NULL,
                    video_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alignment_entries (
                    cache_key TEXT PRIMARY KEY,
                    translation_key TEXT NOT NULL,
                    source_text_hash TEXT NOT NULL,
                    target_text_hash TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    source_tokenizer_json TEXT NOT NULL,
                    target_tokenizer_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value_json TEXT,
                    error_json TEXT,
                    segment_id TEXT NOT NULL,
                    video_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    job_id TEXT,
                    status TEXT NOT NULL,
                    latency_ms REAL,
                    usage_json TEXT,
                    provider_metadata_json TEXT,
                    raw_output TEXT,
                    internal_error TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    cache_key TEXT,
                    segment_id TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    target_language TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
                    position INTEGER NOT NULL,
                    segment_id TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    PRIMARY KEY(batch_id, position)
                );
                CREATE TABLE IF NOT EXISTS stage_counters (
                    stage TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    PRIMARY KEY(stage, name)
                );
                INSERT OR IGNORE INTO stage_counters VALUES ('translation', 'hits', 0);
                INSERT OR IGNORE INTO stage_counters VALUES ('translation', 'misses', 0);
                INSERT OR IGNORE INTO stage_counters VALUES ('alignment', 'hits', 0);
                INSERT OR IGNORE INTO stage_counters VALUES ('alignment', 'misses', 0);
                """
            )
            schema = connection.execute(
                "SELECT value FROM translation_meta WHERE key = 'schema_version'"
            ).fetchone()
            if schema is None or schema[0] not in {"1", "2"}:
                raise ValueError("incompatible translation cache schema; prune the derived cache")
            if schema[0] == "1":
                # A joint payload cannot be decomposed reliably into the two stage caches.
                connection.executescript(
                    """
                    DROP TABLE IF EXISTS cache_entries;
                    DROP TABLE IF EXISTS counters;
                    DELETE FROM batch_jobs;
                    DELETE FROM batches;
                    DELETE FROM jobs;
                    """
                )
            connection.execute(
                "UPDATE translation_meta SET value = '2' WHERE key = 'schema_version'"
            )
            if recover_unfinished:
                connection.execute(
                    "UPDATE jobs SET status = 'interrupted', updated_at = ? "
                    "WHERE status IN ('queued', 'running')",
                    (now(),),
                )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def stage_cached(
        self, stage: str, cache_key: str
    ) -> tuple[str, dict[str, Any] | TranslationErrorInfo] | None:
        table = self._stage_table(stage)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT status, value_json, error_json FROM {table} WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            connection.execute(
                "UPDATE stage_counters SET value = value + 1 WHERE stage = ? AND name = ?",
                (stage, "hits" if row else "misses"),
            )
            if row:
                connection.execute(
                    f"UPDATE {table} SET last_accessed_at = ? WHERE cache_key = ?",
                    (now(), cache_key),
                )
        if row is None:
            return None
        if row["status"] == "complete" and row["value_json"]:
            return "complete", json.loads(row["value_json"])
        if row["status"] == "invalid" and row["error_json"]:
            return "failed", TranslationErrorInfo.model_validate_json(row["error_json"])
        return None

    @staticmethod
    def _stage_table(stage: str) -> str:
        if stage == "translation":
            return "translation_entries"
        if stage == "alignment":
            return "alignment_entries"
        raise ValueError(f"unknown provider stage: {stage}")

    def save_translation(
        self,
        cache_key: str,
        clip: Clip,
        target_language: str,
        provider: str,
        model: str | None,
        prompt_version: str,
        schema_version: int,
        value: dict[str, Any] | None,
        *,
        status: str = "complete",
        error: TranslationErrorInfo | None = None,
    ) -> None:
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO translation_entries VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cache_key,
                    _text_hash(clip.source_text),
                    clip.source_language,
                    target_language,
                    provider,
                    model,
                    prompt_version,
                    schema_version,
                    status,
                    json.dumps(value, ensure_ascii=False) if value is not None else None,
                    error.model_dump_json() if error else None,
                    clip.segment_id,
                    clip.video.video_key,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def save_alignment(
        self,
        cache_key: str,
        translation_key: str,
        clip: Clip,
        target_language: str,
        target_text_hash: str,
        provider: str,
        model: str | None,
        prompt_version: str,
        schema_version: int,
        source_tokenizer: dict[str, Any],
        target_tokenizer: dict[str, Any],
        value: dict[str, Any] | None,
        *,
        status: str = "complete",
        error: TranslationErrorInfo | None = None,
    ) -> None:
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO alignment_entries VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cache_key,
                    translation_key,
                    _text_hash(clip.source_text),
                    target_text_hash,
                    clip.source_language,
                    target_language,
                    provider,
                    model,
                    prompt_version,
                    schema_version,
                    json.dumps(source_tokenizer, sort_keys=True),
                    json.dumps(target_tokenizer, sort_keys=True),
                    status,
                    json.dumps(value, ensure_ascii=False) if value is not None else None,
                    error.model_dump_json() if error else None,
                    clip.segment_id,
                    clip.video.video_key,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def save_attempt(
        self,
        stage: str,
        cache_key: str,
        *,
        job_id: str | None,
        status: str,
        latency_ms: float | None = None,
        usage: dict[str, int] | None = None,
        provider_metadata: dict[str, str] | None = None,
        raw_output: str | None = None,
        internal_error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO provider_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"tra_{uuid.uuid4().hex}",
                    stage,
                    cache_key,
                    job_id,
                    status,
                    latency_ms,
                    json.dumps(usage) if usage else None,
                    json.dumps(provider_metadata) if provider_metadata else None,
                    raw_output[:100_000] if raw_output else None,
                    internal_error,
                    now(),
                ),
            )

    def create_job(
        self,
        segment_id: str,
        target_language: str,
        status: str,
        *,
        cache_key: str | None,
        cache_hit: bool = False,
        result: TranslationResult | None = None,
        error: TranslationErrorInfo | None = None,
    ) -> TranslationJob:
        job_id = f"trn_{uuid.uuid4().hex}"
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    cache_key,
                    segment_id,
                    target_language,
                    status,
                    int(cache_hit),
                    result.model_dump_json() if result else None,
                    error.model_dump_json() if error else None,
                    timestamp,
                    timestamp,
                ),
            )
        return self.job(job_id)

    def update_job(
        self,
        job_id: str,
        status: str,
        *,
        result: TranslationResult | None = None,
        error: TranslationErrorInfo | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, result_json = ?, error_json = ?, updated_at = ? "
                "WHERE job_id = ?",
                (
                    status,
                    result.model_dump_json() if result else None,
                    error.model_dump_json() if error else None,
                    now(),
                    job_id,
                ),
            )

    def job(self, job_id: str) -> TranslationJob:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return TranslationJob(
            job_id=row["job_id"],
            segment_id=row["segment_id"],
            target_language=row["target_language"],
            status=row["status"],
            cache_hit=bool(row["cache_hit"]),
            result=TranslationResult.model_validate_json(row["result_json"])
            if row["result_json"]
            else None,
            error=TranslationErrorInfo.model_validate_json(row["error_json"])
            if row["error_json"]
            else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_batch(self, target_language: str, jobs: list[TranslationJob]) -> str:
        batch_id = f"trb_{uuid.uuid4().hex}"
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO batches VALUES (?, ?, ?, ?)",
                (batch_id, target_language, timestamp, timestamp),
            )
            connection.executemany(
                "INSERT INTO batch_jobs VALUES (?, ?, ?, ?)",
                [
                    (batch_id, position, job.segment_id, job.job_id)
                    for position, job in enumerate(jobs)
                ],
            )
        return batch_id

    def batch(self, batch_id: str) -> TranslationBatch:
        with self.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            rows = connection.execute(
                """SELECT bj.segment_id, j.job_id, j.status, j.cache_hit, j.updated_at
                FROM batch_jobs bj JOIN jobs j ON j.job_id = bj.job_id
                WHERE bj.batch_id = ? ORDER BY bj.position""",
                (batch_id,),
            ).fetchall()
        if batch is None:
            raise KeyError(batch_id)
        counts = {
            state: sum(row["status"] == state for row in rows)
            for state in (
                "queued",
                "running",
                "complete",
                "failed",
                "cancelled",
                "interrupted",
                "unavailable",
            )
        }
        return TranslationBatch(
            batch_id=batch_id,
            target_language=batch["target_language"],
            total=len(rows),
            counts=TranslationBatchCounts(
                total=len(rows), cached=sum(bool(row["cache_hit"]) for row in rows), **counts
            ),
            jobs=[
                TranslationBatchItem(
                    segment_id=row["segment_id"],
                    job_id=row["job_id"],
                    status=row["status"],
                    cache_hit=bool(row["cache_hit"]),
                )
                for row in rows
            ],
            created_at=batch["created_at"],
            updated_at=max([batch["updated_at"], *(row["updated_at"] for row in rows)]),
        )

    def statistics(self, concurrency: int) -> TranslationCacheStatistics:
        with self.connect() as connection:
            translation = dict(
                connection.execute(
                    "SELECT status, COUNT(*) FROM translation_entries GROUP BY status"
                ).fetchall()
            )
            alignment = dict(
                connection.execute(
                    "SELECT status, COUNT(*) FROM alignment_entries GROUP BY status"
                ).fetchall()
            )
            counters = {
                (row[0], row[1]): row[2]
                for row in connection.execute("SELECT stage, name, value FROM stage_counters")
            }
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            attempts = connection.execute("SELECT COUNT(*) FROM provider_attempts").fetchone()[0]
        completed = int(translation.get("complete", 0)) + int(alignment.get("complete", 0))
        failed = int(translation.get("invalid", 0)) + int(alignment.get("invalid", 0))
        return TranslationCacheStatistics(
            completed_entries=completed,
            failed_entries=failed,
            invalid_entries=failed,
            hits=int(counters.get(("translation", "hits"), 0))
            + int(counters.get(("alignment", "hits"), 0)),
            misses=int(counters.get(("translation", "misses"), 0))
            + int(counters.get(("alignment", "misses"), 0)),
            active_jobs=int(active),
            database_bytes=sum(
                candidate.stat().st_size
                for candidate in (
                    self.path,
                    Path(str(self.path) + "-wal"),
                    Path(str(self.path) + "-shm"),
                )
                if candidate.exists()
            ),
            concurrency=concurrency,
            translation_entries=sum(translation.values()),
            alignment_entries=sum(alignment.values()),
            translation_failures=int(translation.get("invalid", 0)),
            alignment_failures=int(alignment.get("invalid", 0)),
            translation_hits=int(counters.get(("translation", "hits"), 0)),
            translation_misses=int(counters.get(("translation", "misses"), 0)),
            alignment_hits=int(counters.get(("alignment", "hits"), 0)),
            alignment_misses=int(counters.get(("alignment", "misses"), 0)),
            provider_attempts=int(attempts),
        )

    def entries(
        self,
        *,
        target_language: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        older_than_days: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses, values = self._filters(target_language, provider, model, older_than_days)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(limit)
        rows: list[dict[str, Any]] = []
        with self.connect() as connection:
            for stage, table in (
                ("translation", "translation_entries"),
                ("alignment", "alignment_entries"),
            ):
                for row in connection.execute(
                    f"""SELECT cache_key, source_text_hash, source_language, target_language,
                    provider, model, prompt_version, schema_version, status, segment_id,
                    video_key, created_at, updated_at, last_accessed_at FROM {table}{where}
                    ORDER BY last_accessed_at DESC LIMIT ?""",
                    values,
                ).fetchall():
                    rows.append({"stage": stage, **dict(row)})
        return sorted(rows, key=lambda row: row["last_accessed_at"], reverse=True)[:limit]

    def prune(
        self,
        *,
        target_language: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        older_than_days: int | None = None,
    ) -> int:
        clauses, values = self._filters(target_language, provider, model, older_than_days)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            return sum(
                connection.execute(f"DELETE FROM {table}{where}", values).rowcount
                for table in ("alignment_entries", "translation_entries")
            )

    @staticmethod
    def _filters(
        target_language: str | None,
        provider: str | None,
        model: str | None,
        older_than_days: int | None,
    ) -> tuple[list[str], list[Any]]:
        if target_language is not None:
            target_language = canonical_language(target_language)
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days must not be negative")
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("target_language", target_language),
            ("provider", provider),
            ("model", model),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if older_than_days is not None:
            clauses.append("last_accessed_at < ?")
            values.append((datetime.now(UTC) - timedelta(days=older_than_days)).isoformat())
        return clauses, values


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
