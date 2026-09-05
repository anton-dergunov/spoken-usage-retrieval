"""Three SQLite layouts used by the multilingual morphology experiment."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from morphology_experiment import GoldSentence

from speech_retrieval.analysis import Analysis, UnicodeAnalyzer
from speech_retrieval.text import normalize_token

MAX_NGRAM = 5


@dataclass(frozen=True)
class StreamToken:
    stream: str
    route: str
    position: int
    sentence_id: str
    start: int
    end: int
    key: str
    surface: str
    lemma: str | None
    upos: str | None


@dataclass(frozen=True)
class Match:
    occurrence_id: str
    sentence_id: str
    start: int
    end: int
    match_type: str


def occurrence_id(sentence_id: str, start: int, end: int) -> str:
    payload = f"{sentence_id}\0{start}\0{end}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def build_streams(
    language: str, sentences: Iterable[GoldSentence], analyses: Iterable[Analysis]
) -> list[StreamToken]:
    rows: list[StreamToken] = []
    unicode_analyzer = UnicodeAnalyzer(language)
    for sentence, analysis in zip(sentences, analyses, strict=True):
        unicode_tokens = unicode_analyzer.analyze(sentence.text).tokens
        for position, token in enumerate(unicode_tokens):
            rows.append(
                StreamToken(
                    f"{sentence.sentence_id}:unicode",
                    "exact",
                    position,
                    sentence.sentence_id,
                    token.start,
                    token.end,
                    token.normalized,
                    token.surface,
                    None,
                    None,
                )
            )

        # Analyzer surface boundaries supplement the Unicode route. Expanded MWT
        # words collapse to their one authoritative source span for exact matching.
        seen_spans: set[tuple[int, int]] = set()
        analyzed_surface: list[StreamToken] = []
        for token in analysis.tokens:
            span = (token.start, token.end)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            analyzed_surface.append(
                StreamToken(
                    f"{sentence.sentence_id}:analyzed-surface",
                    "exact",
                    len(analyzed_surface),
                    sentence.sentence_id,
                    token.start,
                    token.end,
                    token.normalized,
                    token.surface,
                    token.lemma,
                    token.upos,
                )
            )
        unicode_signature = [(token.start, token.end, token.normalized) for token in unicode_tokens]
        analyzed_signature = [(token.start, token.end, token.key) for token in analyzed_surface]
        if analyzed_signature != unicode_signature:
            rows.extend(analyzed_surface)

        # Positions retain holes when a lemma is missing. A SQL adjacency check
        # therefore cannot accidentally bridge over an unanalyzable word.
        for position, token in enumerate(analysis.tokens):
            if not token.lemma:
                continue
            rows.append(
                StreamToken(
                    f"{sentence.sentence_id}:lemma",
                    "lemma",
                    position,
                    sentence.sentence_id,
                    token.start,
                    token.end,
                    token.lemma,
                    token.surface,
                    token.lemma,
                    token.upos,
                )
            )
    return rows


def enumerate_keys(
    streams: Iterable[StreamToken], max_ngram: int = MAX_NGRAM
) -> dict[tuple[str, str], set[Match]]:
    grouped: dict[str, list[StreamToken]] = defaultdict(list)
    for row in streams:
        grouped[row.stream].append(row)
    output: dict[tuple[str, str], set[Match]] = defaultdict(set)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.position)
        for first_index, first in enumerate(rows):
            parts: list[str] = []
            for last_index in range(first_index, min(len(rows), first_index + max_ngram)):
                last = rows[last_index]
                expected = first.position + (last_index - first_index)
                if last.position != expected or last.route != first.route:
                    break
                parts.append(last.key)
                key = " ".join(parts)
                output[(first.route, key)].add(
                    Match(
                        occurrence_id(first.sentence_id, first.start, last.end),
                        first.sentence_id,
                        first.start,
                        last.end,
                        first.route,
                    )
                )
    return output


DUAL_SCHEMA = """
CREATE TABLE occurrences (
    occurrence_id TEXT PRIMARY KEY,
    sentence_id TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE keys (
    occurrence_id TEXT NOT NULL,
    route TEXT NOT NULL CHECK(route IN ('exact', 'lemma')),
    key TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (occurrence_id, route, key)
) WITHOUT ROWID;
CREATE TABLE form_lexicon (
    surface TEXT NOT NULL,
    lemma TEXT NOT NULL,
    upos TEXT,
    frequency INTEGER NOT NULL
);
CREATE UNIQUE INDEX form_identity
    ON form_lexicon(surface, lemma, COALESCE(upos, ''));
"""

TOKEN_SCHEMA = """
CREATE TABLE stream_tokens (
    stream TEXT NOT NULL,
    route TEXT NOT NULL CHECK(route IN ('exact', 'lemma')),
    position INTEGER NOT NULL,
    sentence_id TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    key TEXT NOT NULL,
    surface TEXT NOT NULL,
    lemma TEXT,
    upos TEXT,
    PRIMARY KEY(stream, position)
) WITHOUT ROWID;
CREATE TABLE form_lexicon (
    surface TEXT NOT NULL,
    lemma TEXT NOT NULL,
    upos TEXT,
    frequency INTEGER NOT NULL
);
CREATE UNIQUE INDEX form_identity
    ON form_lexicon(surface, lemma, COALESCE(upos, ''));
CREATE INDEX token_seed ON stream_tokens(route, key, stream, position);
"""


def _insert_lexicon(connection: sqlite3.Connection, streams: Iterable[StreamToken]) -> None:
    frequencies: dict[tuple[str, str, str | None], int] = defaultdict(int)
    for row in streams:
        if row.route == "lemma" and row.lemma:
            frequencies[(normalize_token(row.surface), row.lemma, row.upos)] += 1
    connection.executemany(
        "INSERT INTO form_lexicon VALUES (?, ?, ?, ?)",
        [(*key, count) for key, count in sorted(frequencies.items(), key=str)],
    )


def build_database(path: Path, layout: str, streams: list[StreamToken]) -> dict[str, Any]:
    if layout not in {"dual", "partial", "token"}:
        raise ValueError(f"Unknown layout: {layout}")
    path.unlink(missing_ok=True)
    started = time.perf_counter()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    if layout == "token":
        connection.executescript(TOKEN_SCHEMA)
        connection.executemany(
            "INSERT INTO stream_tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.stream,
                    row.route,
                    row.position,
                    row.sentence_id,
                    row.start,
                    row.end,
                    row.key,
                    row.surface,
                    row.lemma,
                    row.upos,
                )
                for row in streams
            ],
        )
    else:
        connection.executescript(DUAL_SCHEMA)
        all_keys = enumerate_keys(streams)
        occurrences: dict[str, Match] = {}
        key_rows = []
        for (route, key), matches in all_keys.items():
            for match in matches:
                occurrences[match.occurrence_id] = match
                key_rows.append((match.occurrence_id, route, key, key.count(" ") + 1))
        connection.executemany(
            "INSERT INTO occurrences VALUES (?, ?, ?, ?)",
            [
                (match.occurrence_id, match.sentence_id, match.start, match.end)
                for match in sorted(occurrences.values(), key=lambda item: item.occurrence_id)
            ],
        )
        connection.executemany("INSERT INTO keys VALUES (?, ?, ?, ?)", sorted(set(key_rows)))
        if layout == "dual":
            connection.executescript(
                "CREATE INDEX keys_exact ON keys(route, key, n);"
                "CREATE INDEX keys_lemma ON keys(route, n, key);"
            )
        else:
            connection.executescript(
                "CREATE INDEX keys_exact ON keys(key, n) WHERE route='exact';"
                "CREATE INDEX keys_lemma ON keys(n, key) WHERE route='lemma';"
            )
    _insert_lexicon(connection, streams)
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    elapsed = time.perf_counter() - started
    return {
        "layout": layout,
        "path": str(path),
        "build_seconds": round(elapsed, 6),
        "size_bytes": os.path.getsize(path),
        "dbstat_bytes": database_breakdown(path),
        "logical_breakdown": logical_breakdown(path, layout),
    }


def database_breakdown(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY name"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"unavailable": os.path.getsize(path)}
    finally:
        connection.close()
    return {name: size for name, size in rows}


def logical_breakdown(path: Path, layout: str) -> dict[str, dict[str, int]]:
    """Separate routes that share SQLite pages in the dual-key layouts."""
    connection = sqlite3.connect(path)
    output: dict[str, dict[str, int]] = {}
    if layout == "token":
        for route, count, payload in connection.execute(
            """SELECT route, COUNT(*),
                      COALESCE(SUM(LENGTH(stream) + LENGTH(key) + LENGTH(surface)
                          + COALESCE(LENGTH(lemma), 0) + COALESCE(LENGTH(upos), 0)), 0)
               FROM stream_tokens GROUP BY route"""
        ):
            output[f"{route}_token_analysis"] = {
                "rows": count,
                "payload_bytes": payload,
            }
    else:
        occurrence_count, occurrence_payload = connection.execute(
            """SELECT COUNT(*), COALESCE(SUM(LENGTH(occurrence_id)
                      + LENGTH(sentence_id) + 16), 0) FROM occurrences"""
        ).fetchone()
        output["physical_occurrences"] = {
            "rows": occurrence_count,
            "payload_bytes": occurrence_payload,
        }
        for route, count, payload in connection.execute(
            """SELECT route, COUNT(*),
                      COALESCE(SUM(LENGTH(occurrence_id) + LENGTH(key) + 8), 0)
               FROM keys GROUP BY route"""
        ):
            output[f"{route}_keys"] = {"rows": count, "payload_bytes": payload}
    form_count, form_payload = connection.execute(
        """SELECT COUNT(*), COALESCE(SUM(LENGTH(surface) + LENGTH(lemma)
                  + COALESCE(LENGTH(upos), 0) + 8), 0) FROM form_lexicon"""
    ).fetchone()
    output["form_lexicon"] = {"rows": form_count, "payload_bytes": form_payload}
    connection.close()
    return output


def _token_matches(connection: sqlite3.Connection, route: str, key: str) -> set[Match]:
    parts = key.split(" ")
    first_rows = connection.execute(
        """SELECT stream, position, sentence_id, start
           FROM stream_tokens WHERE route=? AND key=?""",
        (route, parts[0]),
    ).fetchall()
    matches: set[Match] = set()
    for stream, position, sentence_id, start in first_rows:
        rows = connection.execute(
            """SELECT key, end FROM stream_tokens
               WHERE stream=? AND position>=? AND position<? ORDER BY position""",
            (stream, position, position + len(parts)),
        ).fetchall()
        if [row[0] for row in rows] != parts:
            continue
        end = rows[-1][1]
        matches.add(Match(occurrence_id(sentence_id, start, end), sentence_id, start, end, route))
    return matches


def search_database(path: Path, layout: str, route: str, key: str) -> set[Match]:
    connection = sqlite3.connect(path)
    try:
        if layout == "token":
            return _token_matches(connection, route, key)
        rows = connection.execute(
            """SELECT o.occurrence_id, o.sentence_id, o.start, o.end
               FROM keys k JOIN occurrences o USING(occurrence_id)
               WHERE k.route=? AND k.key=? AND k.n=?""",
            (route, key, key.count(" ") + 1),
        ).fetchall()
        return {Match(row[0], row[1], row[2], row[3], route) for row in rows}
    finally:
        connection.close()


def auto_search(
    path: Path,
    layout: str,
    exact_keys: Iterable[str],
    lemma_keys: Iterable[str],
) -> set[Match]:
    combined: dict[str, Match] = {}
    for route, keys in (("exact", exact_keys), ("lemma", lemma_keys)):
        for key in keys:
            for match in search_database(path, layout, route, key):
                previous = combined.get(match.occurrence_id)
                if previous is None or (previous.match_type == "lemma" and route == "exact"):
                    combined[match.occurrence_id] = match
    return set(combined.values())


def verify_parity(reference: Path, candidate: Path, candidate_layout: str) -> dict[str, Any]:
    def dual_keys(path: Path) -> dict[tuple[str, str], set[Match]]:
        connection = sqlite3.connect(path)
        rows = connection.execute(
            """SELECT k.route, k.key, o.occurrence_id, o.sentence_id, o.start, o.end
               FROM keys k JOIN occurrences o USING(occurrence_id)"""
        ).fetchall()
        connection.close()
        output: dict[tuple[str, str], set[Match]] = defaultdict(set)
        for route, key, match_id, sentence_id, start, end in rows:
            output[(route, key)].add(Match(match_id, sentence_id, start, end, route))
        return output

    expected_keys = dual_keys(reference)
    if candidate_layout == "token":
        connection = sqlite3.connect(candidate)
        rows = connection.execute(
            """SELECT stream, route, position, sentence_id, start, end, key,
                      surface, lemma, upos
               FROM stream_tokens ORDER BY stream, position"""
        ).fetchall()
        connection.close()
        candidate_keys = enumerate_keys(StreamToken(*row) for row in rows)
    else:
        candidate_keys = dual_keys(candidate)
    failures = []
    all_keys = sorted(set(expected_keys) | set(candidate_keys))
    for route, key in all_keys:
        expected = expected_keys.get((route, key), set())
        actual = candidate_keys.get((route, key), set())
        if expected != actual:
            failures.append(
                {
                    "route": route,
                    "key": key,
                    "expected": sorted(match.occurrence_id for match in expected),
                    "actual": sorted(match.occurrence_id for match in actual),
                }
            )
            if len(failures) == 20:
                break
    return {"passed": not failures, "keys_checked": len(all_keys), "failures": failures}


def warm_query_timings(
    path: Path,
    layout: str,
    queries: Iterable[tuple[str, str]],
    repetitions: int = 20,
) -> dict[str, float | int | None]:
    samples: list[float] = []
    query_list = list(queries)
    for _ in range(repetitions):
        for route, key in query_list:
            started = time.perf_counter()
            search_database(path, layout, route, key)
            samples.append((time.perf_counter() - started) * 1000)
    if not samples:
        return {"samples": 0, "median_ms": None, "p95_ms": None}
    samples.sort()
    p95_index = min(len(samples) - 1, round(0.95 * (len(samples) - 1)))
    return {
        "samples": len(samples),
        "median_ms": round(statistics.median(samples), 6),
        "p95_ms": round(samples[p95_index], 6),
    }
