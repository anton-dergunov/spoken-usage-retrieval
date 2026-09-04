from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .captions import segments_from_files
from .text import accent_key, tokens_with_spans

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE videos (
    video_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    channel_id TEXT,
    channel TEXT NOT NULL,
    channel_config_id TEXT NOT NULL,
    duration REAL,
    upload_date TEXT,
    thumbnail TEXT,
    varieties_json TEXT NOT NULL,
    speech_style_json TEXT NOT NULL,
    caption_kind TEXT NOT NULL,
    caption_language TEXT NOT NULL
);
CREATE TABLE segments (
    segment_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    text TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    clip_start REAL NOT NULL,
    clip_end REAL NOT NULL,
    boundary_reason TEXT NOT NULL,
    boundary_confidence REAL NOT NULL,
    quality_score REAL NOT NULL,
    token_count INTEGER NOT NULL,
    segments_json TEXT NOT NULL
);
CREATE TABLE occurrences (
    occurrence_id TEXT PRIMARY KEY,
    normalized TEXT NOT NULL,
    accent_key TEXT NOT NULL,
    n INTEGER NOT NULL,
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    token_start INTEGER NOT NULL,
    token_end INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    surface TEXT NOT NULL
);
CREATE INDEX occurrences_lookup ON occurrences(normalized, n);
CREATE INDEX occurrences_segment ON occurrences(segment_id);
CREATE TABLE ngram_stats (
    normalized TEXT NOT NULL,
    n INTEGER NOT NULL,
    surface TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    video_count INTEGER NOT NULL,
    PRIMARY KEY(normalized, n)
);
CREATE INDEX ngram_stats_popular ON ngram_stats(n, video_count DESC, total_count DESC);
"""


def _metadata_rows(raw_root: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    rows: list[tuple[Path, Path, dict[str, Any]]] = []
    if not raw_root.exists():
        return rows
    for video_dir in sorted(path for path in raw_root.iterdir() if path.is_dir()):
        metadata_path = video_dir / "metadata.json"
        captions_path = video_dir / "subtitles.raw.json3"
        if not metadata_path.exists() or not captions_path.exists():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append((metadata_path, captions_path, metadata))
    return rows


def _insert_video(connection: sqlite3.Connection, metadata: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata["video_id"],
            metadata.get("provider", "youtube"),
            metadata["url"],
            metadata["title"],
            metadata.get("channel_id"),
            metadata.get("channel") or metadata.get("channel_config_id", "Unknown"),
            metadata.get("channel_config_id", "unknown"),
            metadata.get("duration"),
            metadata.get("upload_date"),
            metadata.get("thumbnail"),
            json.dumps(metadata.get("varieties", []), ensure_ascii=False),
            json.dumps(metadata.get("speech_style", []), ensure_ascii=False),
            metadata.get("caption_kind", "automatic"),
            metadata.get("caption_language", "es"),
        ),
    )


def build_index(*, data_dir: Path, max_ngram: int = 5) -> dict[str, Any]:
    if not 1 <= max_ngram <= 8:
        raise ValueError("max_ngram must be between 1 and 8")
    raw_rows = _metadata_rows(data_dir / "raw" / "videos")
    if not raw_rows:
        raise ValueError(f"no cached transcripts found under {data_dir / 'raw' / 'videos'}")

    index_dir = data_dir / "index"
    derived_dir = data_dir / "derived"
    reports_dir = data_dir / "reports"
    index_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(UTC).isoformat()

    file_descriptor, temporary_name = tempfile.mkstemp(prefix="corpus-", suffix=".sqlite3", dir=index_dir)
    os.close(file_descriptor)
    temporary_db = Path(temporary_name)
    temporary_segments = derived_dir / "segments.jsonl.tmp"
    caption_counts: Counter[str] = Counter()
    boundary_counts: Counter[str] = Counter()
    segment_count = 0
    occurrence_count = 0

    try:
        connection = sqlite3.connect(temporary_db)
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("version", __version__),
                ("built_at", built_at),
                ("max_ngram", str(max_ngram)),
            ],
        )
        with temporary_segments.open("w", encoding="utf-8") as segment_output:
            for metadata_path, captions_path, metadata in raw_rows:
                _insert_video(connection, metadata)
                caption_counts[metadata.get("caption_kind", "automatic")] += 1
                segments = segments_from_files(metadata_path, captions_path)
                for segment in segments:
                    segment_output.write(json.dumps(segment.as_dict(), ensure_ascii=False) + "\n")
                    connection.execute(
                        "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            segment.id,
                            segment.video_id,
                            segment.text,
                            segment.start,
                            segment.end,
                            segment.clip_start,
                            segment.clip_end,
                            segment.boundary_reason,
                            segment.boundary_confidence,
                            segment.quality_score,
                            segment.token_count,
                            json.dumps(
                                [asdict(item) for item in segment.segments],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    boundary_counts[segment.boundary_reason] += 1
                    segment_count += 1
                    tokens = tokens_with_spans(segment.text)
                    for start in range(len(tokens)):
                        for size in range(1, min(max_ngram, len(tokens) - start) + 1):
                            selected = tokens[start : start + size]
                            normalized = " ".join(token.normalized for token in selected)
                            surface = segment.text[selected[0].start : selected[-1].end]
                            occurrence_id = f"{segment.id}:{start}:{size}"
                            connection.execute(
                                "INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    occurrence_id,
                                    normalized,
                                    accent_key(surface),
                                    size,
                                    segment.id,
                                    start,
                                    start + size,
                                    selected[0].start,
                                    selected[-1].end,
                                    surface,
                                ),
                            )
                            occurrence_count += 1
        connection.execute(
            """
            INSERT INTO ngram_stats(normalized, n, surface, total_count, video_count)
            SELECT o.normalized, o.n, MIN(o.surface), COUNT(*), COUNT(DISTINCT s.video_id)
            FROM occurrences o JOIN segments s ON s.segment_id = o.segment_id
            GROUP BY o.normalized, o.n
            """
        )
        connection.commit()
        connection.execute("PRAGMA optimize")
        connection.close()
        temporary_db.replace(index_dir / "corpus.sqlite3")
        temporary_segments.replace(derived_dir / "segments.jsonl")
    except Exception:
        temporary_db.unlink(missing_ok=True)
        temporary_segments.unlink(missing_ok=True)
        raise

    report = {
        "built_at": built_at,
        "version": __version__,
        "max_ngram": max_ngram,
        "video_count": len(raw_rows),
        "segment_count": segment_count,
        "occurrence_count": occurrence_count,
        "caption_kinds": dict(sorted(caption_counts.items())),
        "boundaries": dict(sorted(boundary_counts.items())),
        "database": str(index_dir / "corpus.sqlite3"),
    }
    report_path = reports_dir / "index-build.json"
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_report.replace(report_path)
    return report
