"""SQLite cache for forced-alignment results.

Derived and replaceable: every row can be regenerated from the source caption text and the
prepared clip, so this file may be deleted without losing anything but compute. It lives in
its own database rather than alongside the translation caches, whose ``alignment_entries``
table holds Plan 08's *semantic* translation alignment -- an unrelated concept that happens
to share the word.

Every row records the model and its license. That is what makes a later "remove everything a
non-commercial model produced" a single delete rather than an archaeology exercise.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .alignment import (
    AlignedGroup,
    AlignmentProvenance,
    AlignmentResult,
    AlignmentStatus,
    MatchStatus,
)
from .identity import alignment_id

CACHE_SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS alignment_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forced_alignments (
    cache_key TEXT PRIMARY KEY,
    source_text_hash TEXT NOT NULL,
    source_language TEXT NOT NULL,
    clip_content_sha256 TEXT NOT NULL,
    clip_key TEXT,
    segment_id TEXT,
    video_key TEXT,
    aligner TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_license TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    coverage REAL NOT NULL,
    reason TEXT,
    groups_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forced_alignments_license
    ON forced_alignments (model_license);
CREATE INDEX IF NOT EXISTS forced_alignments_segment
    ON forced_alignments (segment_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class AlignmentRecord:
    """A stored alignment plus the identity it was stored under."""

    cache_key: str
    result: AlignmentResult
    segment_id: str | None
    video_key: str | None
    created_at: str
    updated_at: str


class AlignmentStore:
    """Content-addressed persistence for alignment results."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO alignment_meta VALUES ('schema_version', ?)",
                (str(CACHE_SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def cache_key(
        *,
        source_text: str,
        source_language: str,
        clip_content_sha256: str,
        provenance: AlignmentProvenance,
    ) -> str:
        return alignment_id(
            source_text=source_text,
            source_language=source_language,
            clip_content_sha256=clip_content_sha256,
            aligner=provenance.aligner,
            model_id=provenance.model_id,
            settings_hash=provenance.settings_hash,
        )

    def get(self, cache_key: str) -> AlignmentRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM forced_alignments WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return _record_from_row(row) if row else None

    def find(
        self, *, segment_id: str, source_language: str, model_id: str
    ) -> AlignmentRecord | None:
        """The newest alignment stored for a segment under one model.

        Clip lookup knows a segment id, not the audio checksum that forms the cache key, so this
        is the read path for serving. Ordering by ``updated_at`` means a re-alignment after the
        audio or the settings changed wins over the row it superseded.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM forced_alignments "
                "WHERE segment_id = ? AND source_language = ? AND model_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (segment_id, source_language, model_id),
            ).fetchone()
        return _record_from_row(row) if row else None

    def save(
        self,
        *,
        cache_key: str,
        result: AlignmentResult,
        source_text: str,
        source_language: str,
        clip_content_sha256: str,
        clip_key: str | None = None,
        segment_id: str | None = None,
        video_key: str | None = None,
    ) -> AlignmentRecord:
        """Store ``result``. A failed result is stored too, so it is not retried on every open.

        Retry happens naturally when any key component changes -- different text, different
        audio, a different model, or different settings all produce a different cache key.
        """
        if result.provenance is None:
            raise ValueError("an alignment cannot be cached without provenance")
        stamp = now()
        payload = {
            "cache_key": cache_key,
            "source_text_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "source_language": source_language,
            "clip_content_sha256": clip_content_sha256,
            "clip_key": clip_key,
            "segment_id": segment_id,
            "video_key": video_key,
            "aligner": result.provenance.aligner,
            "model_id": result.provenance.model_id,
            "model_license": result.provenance.model_license,
            "settings_hash": result.provenance.settings_hash,
            "schema_version": result.schema_version,
            "status": result.status,
            "coverage": result.coverage,
            "reason": result.reason,
            "groups_json": json.dumps(
                [group.as_dict() for group in result.groups], ensure_ascii=False
            ),
            "provenance_json": json.dumps(result.provenance.as_dict(), ensure_ascii=False),
            "created_at": stamp,
            "updated_at": stamp,
        }
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in payload if name not in ("cache_key", "created_at")
        )
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO forced_alignments ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(cache_key) DO UPDATE SET {updates}",
                payload,
            )
        record = self.get(cache_key)
        assert record is not None  # just written
        return record

    def statistics(self) -> dict[str, Any]:
        """Counts by status and by license, for ``doctor`` and the experiment report."""
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM forced_alignments").fetchone()[0]
            by_status = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM forced_alignments GROUP BY status"
                )
            }
            by_license = {
                row["model_license"]: row["count"]
                for row in connection.execute(
                    "SELECT model_license, COUNT(*) AS count "
                    "FROM forced_alignments GROUP BY model_license"
                )
            }
        return {
            "total": total,
            "by_status": by_status,
            "by_license": by_license,
            "non_commercial": sum(
                count
                for license_name, count in by_license.items()
                if "nc" in license_name.lower().split("-")
            ),
        }

    def purge_license(self, license_name: str) -> int:
        """Delete every alignment produced by models under ``license_name``.

        This exists so that adopting a commercial footing is a one-command operation rather
        than a reason to distrust the whole cache.
        """
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM forced_alignments WHERE model_license = ?", (license_name,)
            )
            return int(cursor.rowcount)


def _record_from_row(row: sqlite3.Row) -> AlignmentRecord:
    provenance_payload = json.loads(row["provenance_json"])
    groups = tuple(
        AlignedGroup(
            text=item["text"],
            char_start=item["char_start"],
            char_end=item["char_end"],
            start=item["start"],
            end=item["end"],
            match_status=_match_status(item["match_status"]),
            confidence=item.get("confidence"),
        )
        for item in json.loads(row["groups_json"])
    )
    result = AlignmentResult(
        status=_alignment_status(row["status"]),
        groups=groups,
        coverage=row["coverage"],
        provenance=AlignmentProvenance(**provenance_payload),
        reason=row["reason"],
        schema_version=row["schema_version"],
    )
    return AlignmentRecord(
        cache_key=row["cache_key"],
        result=result,
        segment_id=row["segment_id"],
        video_key=row["video_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _alignment_status(value: str) -> AlignmentStatus:
    if value not in ("complete", "partial", "unavailable", "failed"):
        raise ValueError(f"unknown alignment status {value!r}")
    return value  # type: ignore[return-value]


def _match_status(value: str) -> MatchStatus:
    if value not in ("matched", "unmatched", "punctuation"):
        raise ValueError(f"unknown match status {value!r}")
    return value  # type: ignore[return-value]
