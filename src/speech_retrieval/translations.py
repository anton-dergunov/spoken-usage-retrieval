from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .captions import manual_units
from .catalogue import canonical_language
from .contracts import (
    CharacterRange,
    Clip,
    SemanticAlignmentGroup,
    TranslationBatch,
    TranslationBatchCounts,
    TranslationBatchItem,
    TranslationCacheStatistics,
    TranslationErrorInfo,
    TranslationJob,
    TranslationResult,
    TranslationServiceStatus,
)
from .search import Corpus
from .settings import Settings
from .text import join_text

PROMPT_VERSION = "literal-chunks-v3"
TRANSLATION_SCHEMA_VERSION = 1

INSTRUCTIONS = """You translate short speech-caption excerpts for a language learner.

Produce a faithful, literal-leaning translation into the requested target language. Keep the
meaning, register, repetitions, discourse markers, names, and uncertainty of the source. Prefer a
rendering that helps a learner compare the two lines over a freer idiomatic paraphrase. The target
must still be grammatical and natural: preserve source structure only where the target language
allows it, never by reproducing source-language grammar. Do not add explanations.

Return the source and translation as ordered chunks. Concatenating source_chunks.text MUST reproduce
the source text exactly, character for character, including whitespace and punctuation.
Concatenating target_chunks.text is the translation. Give chunks that carry the same semantic
content the same positive group_id, even when their order differs. A group may occur in more than
one chunk on either side. Use group_id 0 only for whitespace, punctuation, or genuinely unaligned
material. Every positive group_id must occur on both sides. Never calculate character offsets; the
application derives them from the exact chunks.

Before returning, silently compare the set of positive group_id values on both sides. The sets must
be identical. If a chunk has no counterpart, label it 0 instead of inventing a one-sided group.
Use warnings only for genuine ambiguity or a defective authored reference, not to explain ordinary
translation or word-order choices.

An authored target-language caption may be supplied as a reference. It can be fluent but incomplete
or freer than the source, so correct it toward the exact source rather than copying it blindly.
"""

GEMINI_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "source_chunks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "group_id": {"type": "INTEGER"},
                },
                "required": ["text", "group_id"],
            },
        },
        "target_chunks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "group_id": {"type": "INTEGER"},
                },
                "required": ["text", "group_id"],
            },
        },
        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["source_chunks", "target_chunks", "warnings"],
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TranslationProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class InvalidTranslationOutput(TranslationProviderError):
    def __init__(self, message: str, raw_output: str = ""):
        super().__init__("invalid_output", message, retryable=False)
        self.raw_output = raw_output[:100_000]


@dataclass(frozen=True)
class ProviderTranslationRequest:
    source_text: str
    source_language: str
    target_language: str
    authored_reference: str | None = None


@dataclass(frozen=True)
class ProviderTranslationResponse:
    payload: dict[str, Any]
    latency_ms: float
    usage: dict[str, int] | None
    raw_output: str
    provider_metadata: dict[str, str] | None = None


class TranslationProvider(Protocol):
    provider: str
    model: str

    async def generate(
        self, request: ProviderTranslationRequest
    ) -> ProviderTranslationResponse: ...

    async def aclose(self) -> None: ...


class GeminiTranslationProvider:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 30.0):
        self.model = model
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def generate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        reference = (
            f"\nAuthored target-language reference (possibly free or incomplete):\n"
            f"{request.authored_reference}"
            if request.authored_reference
            else ""
        )
        user_text = (
            f"Source language: {request.source_language}\n"
            f"Target language: {request.target_language}\n"
            f"Source text:\n{request.source_text}{reference}"
        )
        started = time.perf_counter()
        try:
            response = await self._client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{quote(self.model, safe='')}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
                    "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                        "responseSchema": GEMINI_SCHEMA,
                    },
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise TranslationProviderError(
                "temporarily_unavailable",
                "The translation provider is temporarily unreachable.",
                retryable=True,
            ) from error
        if response.status_code in {401, 403}:
            raise TranslationProviderError(
                "provider_unavailable",
                "The translation provider rejected its API key.",
                retryable=False,
            )
        if response.status_code == 429:
            raise TranslationProviderError(
                "rate_limited", "The translation provider is rate limited.", retryable=True
            )
        if response.status_code == 408 or response.status_code >= 500:
            raise TranslationProviderError(
                "temporarily_unavailable",
                "The translation provider is temporarily unavailable.",
                retryable=True,
            )
        if not response.is_success:
            raise TranslationProviderError(
                "provider_unavailable",
                "The translation provider could not translate this clip.",
                retryable=False,
            )
        try:
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            raw = "".join(str(part.get("text", "")) for part in parts)
            payload = json.loads(raw)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raw = response.text
            raise InvalidTranslationOutput(
                "Gemini returned unreadable structured output.", raw
            ) from error
        usage_payload = body.get("usageMetadata") or {}
        usage = {
            key: int(value)
            for key, value in {
                "input_tokens": usage_payload.get("promptTokenCount"),
                "output_tokens": usage_payload.get("candidatesTokenCount"),
                "total_tokens": usage_payload.get("totalTokenCount"),
            }.items()
            if isinstance(value, int)
        }
        candidate = body.get("candidates", [{}])[0]
        provider_metadata = {
            key: str(value)
            for key, value in {
                "response_id": body.get("responseId"),
                "model_version": body.get("modelVersion"),
                "finish_reason": candidate.get("finishReason")
                if isinstance(candidate, dict)
                else None,
            }.items()
            if value is not None
        }
        return ProviderTranslationResponse(
            payload=payload,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=usage or None,
            raw_output=raw,
            provider_metadata=provider_metadata or None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _chunks(value: Any, name: str) -> list[tuple[str, int]]:
    if not isinstance(value, list) or not value:
        raise InvalidTranslationOutput(f"{name} must be a non-empty array.")
    result: list[tuple[str, int]] = []
    for item in value:
        if not isinstance(item, dict):
            raise InvalidTranslationOutput(f"{name} contains a non-object chunk.")
        text = item.get("text")
        group_id = item.get("group_id")
        if not isinstance(text, str):
            raise InvalidTranslationOutput(f"{name} contains a non-string chunk.")
        if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id < 0:
            raise InvalidTranslationOutput(f"{name} contains an invalid group_id.")
        if not text:
            continue
        result.append((text, group_id))
    if not result:
        raise InvalidTranslationOutput(f"{name} contains no text.")
    return result


def validate_provider_output(
    response: ProviderTranslationResponse,
    request: ProviderTranslationRequest,
    *,
    provider: str,
    model: str,
) -> TranslationResult:
    payload = response.payload
    if not isinstance(payload, dict):
        raise InvalidTranslationOutput("Provider output must be an object.", response.raw_output)
    source_chunks = _chunks(payload.get("source_chunks"), "source_chunks")
    target_chunks = _chunks(payload.get("target_chunks"), "target_chunks")
    if "".join(text for text, _ in source_chunks) != request.source_text:
        raise InvalidTranslationOutput(
            "Source chunks do not reconstruct the source text exactly.", response.raw_output
        )
    target_text = "".join(text for text, _ in target_chunks)
    if not target_text.strip():
        raise InvalidTranslationOutput("Target text is empty.", response.raw_output)
    source_ids = {group for _, group in source_chunks if group > 0}
    target_ids = {group for _, group in target_chunks if group > 0}
    shared_ids = source_ids & target_ids
    if not shared_ids:
        raise InvalidTranslationOutput(
            "Provider output contains no semantic group shared by source and target.",
            response.raw_output,
        )

    def ranges(chunks: list[tuple[str, int]]) -> dict[int, list[CharacterRange]]:
        offset = 0
        grouped: dict[int, list[CharacterRange]] = defaultdict(list)
        for text, group in chunks:
            end = offset + len(text)
            if group > 0:
                grouped[group].append(CharacterRange(start=offset, end=end))
            offset = end
        return grouped

    source_ranges = ranges(source_chunks)
    target_ranges = ranges(target_chunks)
    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise InvalidTranslationOutput("warnings must be an array of strings.", response.raw_output)
    warnings = list(warnings)
    if source_ids != target_ids:
        raise InvalidTranslationOutput(
            "Every positive semantic group must occur in both source and target chunks.",
            response.raw_output,
        )
    return TranslationResult(
        source_language=request.source_language,
        target_language=request.target_language,
        source_text_hash=_hash(request.source_text),
        target_text=target_text,
        alignment_groups=[
            SemanticAlignmentGroup(
                group_id=group_id,
                source_ranges=source_ranges[group_id],
                target_ranges=target_ranges[group_id],
            )
            for group_id in sorted(shared_ids)
        ],
        provenance="llm",
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        schema_version=TRANSLATION_SCHEMA_VERSION,
        warnings=warnings,
        latency_ms=response.latency_ms,
        usage=response.usage,
        provider_metadata=response.provider_metadata,
    )


class TranslationStore:
    def __init__(self, path: Path, *, recover_unfinished: bool = False):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS translation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO translation_meta VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    source_text_hash TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    raw_output TEXT,
                    segment_id TEXT NOT NULL,
                    video_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO counters VALUES ('hits', 0);
                INSERT OR IGNORE INTO counters VALUES ('misses', 0);
                """
            )
            schema = connection.execute(
                "SELECT value FROM translation_meta WHERE key = 'schema_version'"
            ).fetchone()
            if schema is None or schema[0] != "1":
                raise ValueError("incompatible translation cache schema; prune the derived cache")
            if recover_unfinished:
                now = _now()
                connection.execute(
                    "UPDATE jobs SET status = 'interrupted', updated_at = ? "
                    "WHERE status IN ('queued', 'running')",
                    (now,),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def increment(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))

    def cached(self, cache_key: str) -> tuple[str, TranslationResult | TranslationErrorInfo] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, result_json, error_json FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE cache_entries SET last_accessed_at = ? WHERE cache_key = ?",
                    (_now(), cache_key),
                )
        self.increment("hits" if row is not None else "misses")
        if row is None:
            return None
        if row["status"] == "complete" and row["result_json"]:
            return "complete", TranslationResult.model_validate_json(row["result_json"])
        if row["status"] == "invalid" and row["error_json"]:
            return "failed", TranslationErrorInfo.model_validate_json(row["error_json"])
        return None

    def save_result(
        self,
        cache_key: str,
        clip: Clip,
        result: TranslationResult,
        *,
        status: str = "complete",
        error: TranslationErrorInfo | None = None,
        raw_output: str | None = None,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO cache_entries VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cache_key,
                    result.source_text_hash,
                    result.source_language,
                    result.target_language,
                    result.provider,
                    result.model,
                    result.prompt_version,
                    result.schema_version,
                    status,
                    result.model_dump_json() if status == "complete" else None,
                    error.model_dump_json() if error else None,
                    raw_output,
                    clip.segment_id,
                    clip.video.video_key,
                    now,
                    now,
                    now,
                ),
            )

    def save_invalid(
        self,
        cache_key: str,
        clip: Clip,
        target_language: str,
        provider: str,
        model: str,
        error: TranslationErrorInfo,
        raw_output: str,
    ) -> None:
        placeholder = TranslationResult(
            source_language=clip.source_language,
            target_language=target_language,
            source_text_hash=_hash(clip.source_text),
            target_text="invalid",
            alignment_groups=[],
            provenance="llm",
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            schema_version=TRANSLATION_SCHEMA_VERSION,
        )
        self.save_result(
            cache_key, clip, placeholder, status="invalid", error=error, raw_output=raw_output
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
        now = _now()
        with self._connect() as connection:
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
                    now,
                    now,
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
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, result_json = ?, error_json = ?, updated_at = ? "
                "WHERE job_id = ?",
                (
                    status,
                    result.model_dump_json() if result else None,
                    error.model_dump_json() if error else None,
                    _now(),
                    job_id,
                ),
            )

    def job(self, job_id: str) -> TranslationJob:
        with self._connect() as connection:
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
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO batches VALUES (?, ?, ?, ?)", (batch_id, target_language, now, now)
            )
            connection.executemany(
                "INSERT INTO batch_jobs VALUES (?, ?, ?, ?)",
                [(batch_id, index, job.segment_id, job.job_id) for index, job in enumerate(jobs)],
            )
        return batch_id

    def batch(self, batch_id: str) -> TranslationBatch:
        with self._connect() as connection:
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
        counts: dict[str, int] = {
            state: 0
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
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        updated = max([batch["updated_at"], *(row["updated_at"] for row in rows)])
        return TranslationBatch(
            batch_id=batch_id,
            target_language=batch["target_language"],
            total=len(rows),
            counts=TranslationBatchCounts(
                total=len(rows),
                cached=sum(bool(row["cache_hit"]) for row in rows),
                **counts,
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
            updated_at=updated,
        )

    def statistics(self, concurrency: int) -> TranslationCacheStatistics:
        with self._connect() as connection:
            counts = dict(
                connection.execute(
                    "SELECT status, COUNT(*) FROM cache_entries GROUP BY status"
                ).fetchall()
            )
            counters = dict(connection.execute("SELECT name, value FROM counters").fetchall())
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
        return TranslationCacheStatistics(
            completed_entries=int(counts.get("complete", 0)),
            failed_entries=int(counts.get("invalid", 0)),
            invalid_entries=int(counts.get("invalid", 0)),
            hits=int(counters.get("hits", 0)),
            misses=int(counters.get("misses", 0)),
            active_jobs=int(active),
            database_bytes=sum(
                path.stat().st_size
                for path in (
                    self.path,
                    Path(str(self.path) + "-wal"),
                    Path(str(self.path) + "-shm"),
                )
                if path.exists()
            ),
            concurrency=concurrency,
        )

    def prune(
        self,
        *,
        target_language: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        older_than_days: int | None = None,
    ) -> int:
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
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM cache_entries" + where, values)
            return cursor.rowcount

    def entries(
        self,
        *,
        target_language: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        older_than_days: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
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
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT cache_key, source_text_hash, source_language, target_language,
                          provider, model, prompt_version, schema_version, status, segment_id,
                          video_key, created_at, updated_at, last_accessed_at
                   FROM cache_entries"""
                + where
                + " ORDER BY last_accessed_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]


@dataclass
class _SharedOperation:
    task: asyncio.Task[None]
    subscribers: set[str]


def _authored_reference(
    settings: Settings, clip: Clip, target_language: str
) -> tuple[str, dict[str, str]] | None:
    video_dir = settings.data_dir / "raw" / "corpora" / clip.source_language / clip.video.video_key
    manifest_path = video_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    candidates = []
    for track in manifest.get("tracks", []):
        language = track.get("language")
        if (
            track.get("kind") != "authored"
            or track.get("is_source")
            or track.get("status") not in {"downloaded", "cached"}
        ):
            continue
        try:
            normalized = canonical_language(str(language).removesuffix("-orig"))
        except ValueError:
            continue
        priority = (
            0
            if normalized == target_language
            else 1
            if normalized.split("-", 1)[0] == target_language.split("-", 1)[0]
            else 2
        )
        if priority < 2:
            candidates.append((priority, str(track.get("track_id")), normalized, track))
    if not candidates:
        return None
    _, track_id, language, track = min(candidates, key=lambda item: (item[0], item[1]))
    try:
        payload = json.loads(
            (video_dir / track_id / "subtitles.raw.json3").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    text = ""
    for unit in manual_units(payload):
        if unit.end > clip.sentence_start and unit.start < clip.sentence_end:
            text = join_text(text, unit.text)
    if not text:
        return None
    return text, {
        "track_id": track_id,
        "language": language,
        "checksum": str(track.get("content_sha256") or ""),
    }


class TranslationService:
    def __init__(
        self, settings: Settings, corpus: Corpus, provider: TranslationProvider | None = None
    ):
        self.settings = settings
        self.corpus = corpus
        self.provider = provider
        self.store = TranslationStore(
            settings.data_dir / "derived" / "translations.sqlite3", recover_unfinished=True
        )
        self._semaphore = asyncio.Semaphore(settings.translation_concurrency)
        self._operations: dict[str, _SharedOperation] = {}
        self._job_keys: dict[str, str] = {}

    @classmethod
    def configured(
        cls, settings: Settings, corpus: Corpus, provider: TranslationProvider | None = None
    ) -> TranslationService:
        if provider is None and settings.gemini_api_key:
            provider = GeminiTranslationProvider(
                settings.gemini_api_key,
                settings.translation_model,
                timeout_seconds=settings.translation_timeout_seconds,
            )
        return cls(settings, corpus, provider)

    def _cache_key(self, clip: Clip, target_language: str) -> str:
        assert self.provider is not None
        return _hash(
            "\0".join(
                (
                    _hash(clip.source_text),
                    clip.source_language,
                    target_language,
                    self.provider.provider,
                    self.provider.model,
                    PROMPT_VERSION,
                    str(TRANSLATION_SCHEMA_VERSION),
                )
            )
        )

    def validate(self, segment_id: str, target_language: str) -> tuple[Clip, str]:
        target = canonical_language(target_language)
        clip = self.corpus.clip(segment_id)
        if target == clip.source_language:
            raise ValueError("target language must differ from the source language")
        return clip, target

    async def request(self, segment_id: str, target_language: str) -> TranslationJob:
        clip, target = self.validate(segment_id, target_language)
        authored = _authored_reference(self.settings, clip, target)
        if self.provider is None:
            if authored is None:
                return self.store.create_job(
                    segment_id,
                    target,
                    "unavailable",
                    cache_key=None,
                    error=TranslationErrorInfo(
                        code="provider_unavailable",
                        message="No translation provider or authored target-language track is available.",
                    ),
                )
            text, metadata = authored
            result = TranslationResult(
                source_language=clip.source_language,
                target_language=target,
                source_text_hash=_hash(clip.source_text),
                target_text=text,
                alignment_groups=[],
                provenance="authored_track",
                provider="youtube",
                model=None,
                prompt_version="authored-track-v1",
                schema_version=TRANSLATION_SCHEMA_VERSION,
                authored_track_language=metadata["language"],
                authored_track_id=metadata["track_id"],
                warnings=["Authored caption fallback has no semantic alignment."],
                provider_metadata={"video_id": clip.video.id},
            )
            cache_key = _hash(
                "\0".join(
                    (
                        _hash(clip.source_text),
                        clip.source_language,
                        target,
                        "authored_track",
                        metadata["checksum"],
                        "authored-track-v1",
                        str(TRANSLATION_SCHEMA_VERSION),
                    )
                )
            )
            cached = self.store.cached(cache_key)
            if cached and cached[0] == "complete":
                result = cached[1]  # type: ignore[assignment]
            else:
                self.store.save_result(cache_key, clip, result)
            return self.store.create_job(
                segment_id,
                target,
                "complete",
                cache_key=cache_key,
                cache_hit=cached is not None,
                result=result,
            )

        cache_key = self._cache_key(clip, target)
        cached = self.store.cached(cache_key)
        if cached is not None:
            status, value = cached
            return self.store.create_job(
                segment_id,
                target,
                status,
                cache_key=cache_key,
                cache_hit=True,
                result=value if isinstance(value, TranslationResult) else None,
                error=value if isinstance(value, TranslationErrorInfo) else None,
            )
        job = self.store.create_job(segment_id, target, "queued", cache_key=cache_key)
        self._job_keys[job.job_id] = cache_key
        operation = self._operations.get(cache_key)
        if operation is not None:
            operation.subscribers.add(job.job_id)
            return job
        task = asyncio.create_task(self._run(cache_key, clip, target, authored))
        self._operations[cache_key] = _SharedOperation(task=task, subscribers={job.job_id})
        return job

    async def _run(
        self,
        cache_key: str,
        clip: Clip,
        target: str,
        authored: tuple[str, dict[str, str]] | None,
    ) -> None:
        operation = self._operations[cache_key]
        try:
            async with self._semaphore:
                for job_id in list(operation.subscribers):
                    self.store.update_job(job_id, "running")
                assert self.provider is not None
                request = ProviderTranslationRequest(
                    source_text=clip.source_text,
                    source_language=clip.source_language,
                    target_language=target,
                    authored_reference=authored[0] if authored else None,
                )
                response = await self.provider.generate(request)
                result = validate_provider_output(
                    response, request, provider=self.provider.provider, model=self.provider.model
                )
                if authored:
                    result = result.model_copy(
                        update={
                            "authored_track_language": authored[1]["language"],
                            "authored_track_id": authored[1]["track_id"],
                        }
                    )
                self.store.save_result(cache_key, clip, result)
                for job_id in list(operation.subscribers):
                    self.store.update_job(job_id, "complete", result=result)
        except asyncio.CancelledError:
            for job_id in list(operation.subscribers):
                if self.store.job(job_id).status not in {"cancelled", "complete"}:
                    self.store.update_job(job_id, "interrupted")
            raise
        except TranslationProviderError as error:
            info = TranslationErrorInfo(
                code=error.code, message=str(error), retryable=error.retryable
            )
            if isinstance(error, InvalidTranslationOutput):
                assert self.provider is not None
                self.store.save_invalid(
                    cache_key,
                    clip,
                    target,
                    self.provider.provider,
                    self.provider.model,
                    info,
                    error.raw_output,
                )
            for job_id in list(operation.subscribers):
                self.store.update_job(job_id, "failed", error=info)
        except Exception:
            info = TranslationErrorInfo(
                code="temporarily_unavailable",
                message="The translation provider failed unexpectedly.",
                retryable=True,
            )
            for job_id in list(operation.subscribers):
                self.store.update_job(job_id, "failed", error=info)
        finally:
            self._operations.pop(cache_key, None)

    def job(self, job_id: str) -> TranslationJob:
        return self.store.job(job_id)

    async def cancel(self, job_id: str) -> TranslationJob:
        job = self.store.job(job_id)
        if job.status not in {"queued", "running"}:
            return job
        self.store.update_job(job_id, "cancelled")
        cache_key = self._job_keys.get(job_id)
        operation = self._operations.get(cache_key or "")
        if operation:
            operation.subscribers.discard(job_id)
            if not operation.subscribers:
                operation.task.cancel()
        return self.store.job(job_id)

    async def create_batch(self, segment_ids: list[str], target_language: str) -> TranslationBatch:
        if not 1 <= len(segment_ids) <= 50:
            raise ValueError("translation batches require between 1 and 50 segment_ids")
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("segment_ids must be unique")
        validated = [self.validate(segment_id, target_language) for segment_id in segment_ids]
        target = validated[0][1]
        jobs = [await self.request(clip.segment_id, target) for clip, _ in validated]
        return self.store.batch(self.store.create_batch(target, jobs))

    def batch(self, batch_id: str) -> TranslationBatch:
        return self.store.batch(batch_id)

    async def cancel_batch(self, batch_id: str) -> TranslationBatch:
        batch = self.store.batch(batch_id)
        for item in batch.jobs:
            await self.cancel(item.job_id)
        return self.store.batch(batch_id)

    def status(self) -> TranslationServiceStatus:
        return TranslationServiceStatus(
            provider_available=self.provider is not None,
            provider=self.provider.provider if self.provider else None,
            model=self.provider.model if self.provider else None,
            target_languages=list(self.settings.translation_target_languages),
            default_target_language=self.settings.default_target_language,
            cache=self.store.statistics(self.settings.translation_concurrency),
        )

    async def aclose(self) -> None:
        tasks = [operation.task for operation in self._operations.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.provider is not None:
            await self.provider.aclose()
