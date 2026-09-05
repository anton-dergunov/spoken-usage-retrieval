from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .analysis import Analysis, AnalyzedToken, get_analyzer
from .captions import segments_from_files
from .identity import (
    CACHE_SCHEMA_VERSION,
    DATABASE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
)
from .text import accent_key, tokens_with_spans

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE videos (
    video_key TEXT PRIMARY KEY,
    source_language TEXT NOT NULL,
    provider TEXT NOT NULL,
    video_id TEXT NOT NULL,
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
    UNIQUE(source_language, provider, video_id)
);
CREATE INDEX videos_language ON videos(source_language, channel_config_id, video_key);
CREATE TABLE transcripts (
    track_id TEXT PRIMARY KEY,
    source_language TEXT NOT NULL,
    video_key TEXT NOT NULL REFERENCES videos(video_key),
    caption_kind TEXT NOT NULL,
    caption_language TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    cache_schema_version INTEGER NOT NULL,
    catalogue_schema_version INTEGER NOT NULL
);
CREATE INDEX transcripts_language ON transcripts(source_language, video_key, track_id);
CREATE TABLE segments (
    segment_id TEXT PRIMARY KEY,
    source_language TEXT NOT NULL,
    track_id TEXT NOT NULL REFERENCES transcripts(track_id),
    video_key TEXT NOT NULL REFERENCES videos(video_key),
    text TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    clip_start REAL NOT NULL,
    clip_end REAL NOT NULL,
    boundary_reason TEXT NOT NULL,
    boundary_confidence REAL NOT NULL,
    quality_score REAL NOT NULL,
    token_count INTEGER NOT NULL,
    segments_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    analyzed_token_count INTEGER NOT NULL
);
CREATE INDEX segments_language ON segments(source_language, video_key, segment_id);
CREATE TABLE occurrences (
    occurrence_id TEXT PRIMARY KEY,
    source_language TEXT NOT NULL,
    normalized TEXT NOT NULL,
    accent_key TEXT NOT NULL,
    n INTEGER NOT NULL,
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    token_start INTEGER NOT NULL,
    token_end INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    surface TEXT NOT NULL,
    token_count INTEGER NOT NULL
);
CREATE INDEX occurrences_lookup ON occurrences(source_language, normalized, n);
CREATE INDEX occurrences_segment ON occurrences(source_language, segment_id);
CREATE TABLE analyzers (
    source_language TEXT PRIMARY KEY,
    provenance_json TEXT NOT NULL,
    morphology_available INTEGER NOT NULL,
    unavailable_reason TEXT
);
CREATE TABLE occurrence_keys (
    key_id INTEGER PRIMARY KEY,
    source_language TEXT NOT NULL,
    occurrence_id TEXT NOT NULL REFERENCES occurrences(occurrence_id),
    kind TEXT NOT NULL CHECK(kind IN ('exact', 'lemma')),
    normalized TEXT NOT NULL,
    n INTEGER NOT NULL,
    lemmas_json TEXT,
    first_lemma TEXT,
    suggestion_eligible INTEGER NOT NULL DEFAULT 0,
    UNIQUE(occurrence_id, kind, normalized, n)
);
CREATE INDEX keys_surface ON occurrence_keys(source_language, kind, normalized, n);
CREATE INDEX keys_lemma ON occurrence_keys(source_language, kind, n, first_lemma);
CREATE TABLE form_lexicon (
    source_language TEXT NOT NULL,
    normalized TEXT NOT NULL,
    lemma TEXT NOT NULL,
    upos TEXT,
    frequency INTEGER NOT NULL
);
CREATE INDEX forms_lemma ON form_lexicon(source_language, lemma);
CREATE UNIQUE INDEX forms_unique
    ON form_lexicon(source_language, normalized, lemma, COALESCE(upos, ''));
CREATE TABLE ngram_stats (
    source_language TEXT NOT NULL,
    normalized TEXT NOT NULL,
    n INTEGER NOT NULL,
    surface TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    video_count INTEGER NOT NULL,
    PRIMARY KEY(source_language, normalized, n)
);
CREATE INDEX ngram_stats_popular
    ON ngram_stats(source_language, n, video_count DESC, total_count DESC);
"""


def _metadata_rows(raw_root: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    rows: list[tuple[Path, Path, dict[str, Any]]] = []
    if not raw_root.exists():
        return rows
    for metadata_path in sorted(raw_root.glob("*/*/*/metadata.json")):
        captions_path = metadata_path.with_name("subtitles.raw.json3")
        if not captions_path.exists():
            raise ValueError(f"cached transcript has no captions: {metadata_path.parent}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid cached transcript metadata: {metadata_path}") from error
        expected_language = metadata_path.parents[2].name
        expected_video_key = metadata_path.parents[1].name
        expected_track_id = metadata_path.parent.name
        checks = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "source_language": expected_language,
            "video_key": expected_video_key,
            "track_id": expected_track_id,
        }
        for field, expected in checks.items():
            if metadata.get(field) != expected:
                raise ValueError(
                    f"{metadata_path}: {field} must be {expected!r}, got {metadata.get(field)!r}"
                )
        caption_checksum = hashlib.sha256(captions_path.read_bytes()).hexdigest()
        if metadata.get("content_sha256") != caption_checksum:
            raise ValueError(f"{captions_path}: content checksum does not match metadata")
        rows.append((metadata_path, captions_path, metadata))
    return rows


def _insert_video(connection: sqlite3.Connection, metadata: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO videos(
            video_key, source_language, provider, video_id, url, title, channel_id, channel,
            channel_config_id, duration, upload_date, thumbnail, varieties_json, speech_style_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata["video_key"],
            metadata["source_language"],
            metadata["provider"],
            metadata["video_id"],
            metadata["url"],
            metadata["title"],
            metadata.get("channel_id"),
            metadata.get("channel") or metadata["channel_config_id"],
            metadata["channel_config_id"],
            metadata.get("duration"),
            metadata.get("upload_date"),
            metadata.get("thumbnail"),
            json.dumps(metadata.get("varieties", []), ensure_ascii=False),
            json.dumps(metadata.get("speech_style", []), ensure_ascii=False),
        ),
    )
    connection.execute(
        """
        INSERT INTO transcripts(
            track_id, source_language, video_key, caption_kind, caption_language, content_sha256,
            cache_schema_version, catalogue_schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metadata["track_id"],
            metadata["source_language"],
            metadata["video_key"],
            metadata["caption_kind"],
            metadata["caption_language"],
            metadata["content_sha256"],
            metadata["cache_schema_version"],
            metadata["catalogue_schema_version"],
        ),
    )


def _insert_occurrences(
    connection: sqlite3.Connection,
    segment_id: str,
    language: str,
    text: str,
    analysis: Analysis,
    max_ngram: int,
) -> int:
    # One physical occurrence can have several surface/lemma keys (including MWT spans).
    spans: dict[tuple[int, int], str] = {}

    def insert_key(
        selected,
        start: int,
        kind: str,
        normalized: str,
        lemmas=None,
        *,
        suggestion=False,
        token_count=None,
    ):
        bounds = (selected[0].start, selected[-1].end)
        occurrence_id = spans.get(bounds)
        if occurrence_id is None:
            occurrence_id = f"{segment_id}:{bounds[0]}:{bounds[1]}"
            spans[bounds] = occurrence_id
            surface = text[bounds[0] : bounds[1]]
            connection.execute(
                "INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    occurrence_id,
                    language,
                    normalized,
                    accent_key(surface),
                    len(selected),
                    segment_id,
                    start,
                    start + len(selected),
                    *bounds,
                    surface,
                    token_count if token_count is not None else len(analysis.tokens),
                ),
            )
        connection.execute(
            """INSERT INTO occurrence_keys(
                source_language, occurrence_id, kind, normalized, n, lemmas_json, first_lemma,
                suggestion_eligible
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(occurrence_id, kind, normalized, n) DO UPDATE
            SET suggestion_eligible = MAX(suggestion_eligible, excluded.suggestion_eligible)""",
            (
                language,
                occurrence_id,
                kind,
                normalized,
                len(selected),
                json.dumps(lemmas, ensure_ascii=False) if lemmas else None,
                lemmas[0] if lemmas else None,
                suggestion,
            ),
        )

    # Retain the entire original regex inventory, independently of toolkit tokenization.
    legacy = tokens_with_spans(text)
    for start in range(len(legacy)):
        for size in range(1, min(max_ngram, len(legacy) - start) + 1):
            selected = legacy[start : start + size]
            insert_key(
                selected,
                start,
                "exact",
                " ".join(token.normalized for token in selected),
                token_count=len(legacy),
            )

    # Surface words use source-token boundaries, not synthetic MWT word strings.
    surfaces: list[AnalyzedToken] = []
    for token in analysis.tokens:
        if not surfaces or (surfaces[-1].start, surfaces[-1].end) != (token.start, token.end):
            surfaces.append(token)
        if token.lemma:
            connection.execute(
                """INSERT INTO form_lexicon VALUES (?, ?, ?, ?, 1)
                ON CONFLICT DO UPDATE SET frequency = frequency + 1""",
                (language, token.normalized, token.lemma, token.upos),
            )
    for start in range(len(surfaces)):
        for size in range(1, min(max_ngram, len(surfaces) - start) + 1):
            selected_surface = surfaces[start : start + size]
            insert_key(
                selected_surface,
                start,
                "exact",
                " ".join(t.normalized for t in selected_surface),
                suggestion=True,
                token_count=len(surfaces),
            )
    for start in range(len(analysis.tokens)):
        for size in range(1, min(max_ngram, len(analysis.tokens) - start) + 1):
            selected_words = analysis.tokens[start : start + size]
            if all(token.lemma for token in selected_words):
                lemmas = [token.lemma for token in selected_words]
                insert_key(
                    selected_words, start, "lemma", " ".join(str(lemma) for lemma in lemmas), lemmas
                )
    return len(spans)


def build_index(
    *, data_dir: Path, max_ngram: int = 5, analyzer: str = "auto", models_dir: Path | None = None
) -> dict[str, Any]:
    if not 1 <= max_ngram <= 8:
        raise ValueError("max_ngram must be between 1 and 8")
    raw_rows = _metadata_rows(data_dir / "raw" / "corpora")
    if not raw_rows:
        location = data_dir / "raw" / "corpora"
        raise ValueError(f"no versioned cached transcripts found under {location}")

    models_dir = (models_dir or data_dir / "models" / "stanza").resolve()
    analyzers = {
        language: get_analyzer(language, analyzer, str(models_dir))
        for language in sorted({row[2]["source_language"] for row in raw_rows})
    }
    analyzer_ids = {language: item.provenance.identity for language, item in analyzers.items()}
    index_dir = data_dir / "index"
    derived_root = data_dir / "derived" / "corpora"
    reports_dir = data_dir / "reports"
    index_dir.mkdir(parents=True, exist_ok=True)
    derived_root.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(UTC).isoformat()

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="corpus-", suffix=".sqlite3", dir=index_dir
    )
    os.close(file_descriptor)
    temporary_db = Path(temporary_name)
    temporary_segments: dict[str, Path] = {}
    segment_outputs: dict[str, TextIO] = {}
    language_counts: dict[str, Counter[str]] = defaultdict(Counter)
    caption_counts: Counter[str] = Counter()
    boundary_counts: Counter[str] = Counter()
    segment_count = 0
    occurrence_count = 0
    connection: sqlite3.Connection | None = None

    try:
        connection = sqlite3.connect(temporary_db)
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(DATABASE_SCHEMA_VERSION)),
                ("package_version", __version__),
                ("built_at", built_at),
                ("max_ngram", str(max_ngram)),
                (
                    "analyzer_id",
                    next(iter(analyzer_ids.values())) if len(analyzer_ids) == 1 else "mixed",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO analyzers VALUES (?, ?, ?, ?)",
            [
                (
                    language,
                    json.dumps(item.provenance.as_dict(), ensure_ascii=False, sort_keys=True),
                    item.morphology_available,
                    item.unavailable_reason,
                )
                for language, item in analyzers.items()
            ],
        )
        for metadata_path, captions_path, metadata in raw_rows:
            language = metadata["source_language"]
            if language not in segment_outputs:
                language_dir = derived_root / language
                language_dir.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(
                    prefix="segments-", suffix=".jsonl.tmp", dir=language_dir
                )
                segment_outputs[language] = os.fdopen(descriptor, "w", encoding="utf-8")
                temporary_segments[language] = Path(name)
            _insert_video(connection, metadata)
            caption_counts[metadata["caption_kind"]] += 1
            language_counts[language]["videos"] += 1
            language_counts[language][f"caption:{metadata['caption_kind']}"] += 1
            segments = segments_from_files(metadata_path, captions_path)
            for segment in segments:
                analysis = analyzers[language].analyze(segment.text).validate(segment.text)
                analysis_json = json.dumps(
                    analysis.as_dict(), ensure_ascii=False, separators=(",", ":")
                )
                segment_outputs[language].write(
                    json.dumps(
                        {
                            **segment.as_dict(),
                            "analysis": analysis.as_dict(),
                            "analysis_schema_version": 1,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                connection.execute(
                    """
                    INSERT INTO segments(
                        segment_id, source_language, track_id, video_key, text, start, end,
                        clip_start, clip_end, boundary_reason, boundary_confidence, quality_score,
                        token_count, segments_json, analysis_json, analyzed_token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        segment.id,
                        segment.source_language,
                        segment.track_id,
                        segment.video_key,
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
                        analysis_json,
                        len(analysis.tokens),
                    ),
                )
                boundary_counts[segment.boundary_reason] += 1
                language_counts[language]["segments"] += 1
                segment_count += 1
                count = _insert_occurrences(
                    connection, segment.id, language, segment.text, analysis, max_ngram
                )
                occurrence_count += count
                language_counts[language]["occurrences"] += count
        connection.execute(
            """
            INSERT INTO ngram_stats(
                source_language, normalized, n, surface, total_count, video_count
            )
            SELECT k.source_language, k.normalized, k.n, MIN(o.surface), COUNT(*),
                   COUNT(DISTINCT s.video_key)
            FROM occurrence_keys k JOIN occurrences o USING(occurrence_id)
            JOIN segments s ON s.segment_id = o.segment_id
            WHERE k.kind = 'exact' AND k.suggestion_eligible = 1
            GROUP BY k.source_language, k.normalized, k.n
            """
        )
        connection.commit()
        connection.execute("PRAGMA optimize")
        connection.close()
        connection = None
        for output in segment_outputs.values():
            output.close()
        temporary_db.replace(index_dir / "corpus.sqlite3")
        for language, temporary_path in temporary_segments.items():
            temporary_path.replace(derived_root / language / "segments.jsonl")
    except Exception:
        if connection is not None:
            connection.close()
        for output in segment_outputs.values():
            if not output.closed:
                output.close()
        temporary_db.unlink(missing_ok=True)
        for temporary_path in temporary_segments.values():
            temporary_path.unlink(missing_ok=True)
        raise

    languages: dict[str, Any] = {}
    for language, counts in sorted(language_counts.items()):
        languages[language] = {
            "videos": counts["videos"],
            "segments": counts["segments"],
            "occurrences": counts["occurrences"],
            "caption_kinds": {
                key.removeprefix("caption:"): value
                for key, value in sorted(counts.items())
                if key.startswith("caption:")
            },
            "analyzer_id": analyzer_ids[language],
            "analyzer": analyzers[language].provenance.as_dict(),
            "morphology_available": analyzers[language].morphology_available,
        }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "built_at": built_at,
        "package_version": __version__,
        "analyzer_id": next(iter(analyzer_ids.values())) if len(analyzer_ids) == 1 else "mixed",
        "analyzers": {language: item.provenance.as_dict() for language, item in analyzers.items()},
        "max_ngram": max_ngram,
        "video_count": len(raw_rows),
        "segment_count": segment_count,
        "occurrence_count": occurrence_count,
        "caption_kinds": dict(sorted(caption_counts.items())),
        "boundaries": dict(sorted(boundary_counts.items())),
        "languages": languages,
        "database": str(index_dir / "corpus.sqlite3"),
    }
    report_path = reports_dir / "index-build.json"
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_report.replace(report_path)
    return report
