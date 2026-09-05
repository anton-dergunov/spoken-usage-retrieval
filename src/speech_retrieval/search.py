from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .analysis import (
    AnalyzedToken,
    IncompatibleAnalyzerError,
    UnicodeAnalyzer,
    UnsupportedAnalysisError,
    recorded_analyzer,
)
from .catalogue import canonical_language, load_catalogue_directory
from .identity import DATABASE_SCHEMA_VERSION
from .stopwords import stopwords
from .text import accent_key, normalized_query


class SearchError(ValueError):
    pass


class IncompatibleIndexError(RuntimeError):
    pass


class Corpus:
    def __init__(
        self,
        data_dir: str | Path,
        catalogue_dir: str | Path = "config/channels",
        *,
        models_dir: str | Path | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.catalogue_dir = Path(catalogue_dir)
        self.models_dir = Path(models_dir or self.data_dir / "models" / "stanza").resolve()
        self.database = self.data_dir / "index" / "corpus.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        if not self.database.exists():
            raise FileNotFoundError(f"search index not found: {self.database}")
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error as error:
            connection.close()
            raise IncompatibleIndexError(
                "search index predates the versioned corpus schema; rebuild it"
            ) from error
        if meta.get("schema_version") != str(DATABASE_SCHEMA_VERSION):
            connection.close()
            raise IncompatibleIndexError(
                "incompatible search index schema; rebuild the corpus index"
            )
        return connection

    @staticmethod
    def _source_language(value: str) -> str:
        try:
            return canonical_language(value)
        except ValueError as error:
            raise SearchError(str(error)) from error

    @staticmethod
    def _require_indexed(connection: sqlite3.Connection, source_language: str) -> None:
        indexed = connection.execute(
            "SELECT 1 FROM videos WHERE source_language = ? LIMIT 1", (source_language,)
        ).fetchone()
        if indexed is None:
            raise SearchError(f"Source language is not indexed: {source_language}")

    def search(
        self, query: str, *, source_language: str, match_mode: str = "auto", limit: int = 20
    ) -> dict[str, Any]:
        source_language = self._source_language(source_language)
        if match_mode not in ("exact", "lemma", "auto"):
            raise SearchError("match_mode must be exact, lemma, or auto")
        normalized, query_tokens = normalized_query(query)
        if not query_tokens:
            raise SearchError("Enter a word or phrase to search for.")
        if len(query_tokens) > 5:
            raise SearchError("This prototype searches phrases of up to five words.")
        limit = max(1, min(int(limit), 50))
        query_accent_key = accent_key(query)
        with closing(self._connect()) as connection:
            self._require_indexed(connection, source_language)
            metadata = connection.execute(
                "SELECT * FROM analyzers WHERE source_language = ?", (source_language,)
            ).fetchone()
            provenance = json.loads(metadata["provenance_json"])
            reason = metadata["unavailable_reason"]
            try:
                analyzer = recorded_analyzer(provenance, self.models_dir)
            except UnsupportedAnalysisError as error:
                analyzer = UnicodeAnalyzer(source_language, str(error))
                reason = str(error)
            except IncompatibleAnalyzerError as error:
                raise IncompatibleIndexError(str(error)) from error
            morphology_available = analyzer.morphology_available
            if match_mode == "lemma" and not morphology_available:
                raise UnsupportedAnalysisError(reason or analyzer.unavailable_reason)
            analysis = analyzer.analyze(query).validate(query)
            if len(analysis.tokens) > 5 and match_mode != "exact":
                raise SearchError("This prototype searches phrases of up to five analyzed words.")
            query_analyses = []
            candidate_sets = []
            for position, token in enumerate(analysis.tokens):
                candidates_by_pair: dict[tuple[str, str | None], dict[str, Any]] = {}
                if token.lemma:
                    candidates_by_pair[token.lemma, token.upos] = {
                        "lemma": token.lemma,
                        "upos": token.upos,
                        "sources": ["query_analyzer"],
                        "frequency": 0,
                        "analyzer": provenance,
                    }
                if morphology_available:
                    observed = connection.execute(
                        """SELECT lemma, upos, frequency FROM form_lexicon
                        WHERE source_language = ? AND normalized = ? ORDER BY lemma, COALESCE(upos, '')""",
                        (source_language, token.normalized),
                    ).fetchall()
                    for item in observed:
                        pair = item["lemma"], item["upos"]
                        candidate = candidates_by_pair.setdefault(
                            pair,
                            {
                                "lemma": item["lemma"],
                                "upos": item["upos"],
                                "sources": [],
                                "frequency": 0,
                                "analyzer": provenance,
                            },
                        )
                        candidate["sources"].append("corpus")
                        candidate["frequency"] = item["frequency"]
                if morphology_available:
                    # A dictionary-form query may itself receive a different analysis
                    # (Portuguese "casa" -> "casar"). Retain corpus-attested lemmas too.
                    for item in connection.execute(
                        "SELECT DISTINCT upos FROM form_lexicon WHERE source_language = ? AND lemma = ?",
                        (source_language, token.normalized),
                    ):
                        candidate = candidates_by_pair.setdefault(
                            (token.normalized, item["upos"]),
                            {
                                "lemma": token.normalized,
                                "upos": item["upos"],
                                "sources": [],
                                "frequency": 0,
                                "analyzer": provenance,
                            },
                        )
                        candidate["sources"].append("indexed_lemma")
                candidates_for_token = sorted(
                    candidates_by_pair.values(), key=lambda c: (c["lemma"], c["upos"] or "")
                )
                query_analyses.append(
                    {
                        "position": position,
                        "token": asdict(token),
                        "candidates": candidates_for_token,
                        "ambiguous": len(candidates_for_token) > 1,
                    }
                )
                candidate_sets.append(sorted({c["lemma"] for c in candidates_for_token}))

            surface_keys = {(normalized, len(query_tokens))}
            surfaces: list[AnalyzedToken] = []
            for token in analysis.tokens:
                if not surfaces or (surfaces[-1].start, surfaces[-1].end) != (
                    token.start,
                    token.end,
                ):
                    surfaces.append(token)
            if surfaces and len(surfaces) <= 5:
                surface_keys.add((" ".join(token.normalized for token in surfaces), len(surfaces)))
            matches: dict[str, dict[str, Any]] = {}
            for key, size in sorted(surface_keys):
                for row in connection.execute(
                    """SELECT occurrence_id FROM occurrence_keys
                    WHERE source_language = ? AND kind = 'exact' AND normalized = ? AND n = ?""",
                    (source_language, key, size),
                ):
                    matches[row["occurrence_id"]] = {
                        "exact": True,
                        "lemma": False,
                        "matched_lemma": None,
                    }
            if morphology_available and candidate_sets and all(candidate_sets):
                # Index the first position, then check remaining candidate sets in SQL.
                # JSON arrays avoid SQLite's variable limit and Cartesian expansion.
                for row in connection.execute(
                    """SELECT occurrence_id, normalized FROM occurrence_keys k
                    WHERE source_language = ? AND kind = 'lemma' AND n = ?
                    AND first_lemma IN (SELECT value FROM json_each(?))
                    AND NOT EXISTS (
                        SELECT 1 FROM json_each(k.lemmas_json) term
                        WHERE term.value NOT IN (
                            SELECT value FROM json_each(json_extract(?, '$[' || term.key || ']'))
                        )
                    ) ORDER BY occurrence_id, normalized""",
                    (
                        source_language,
                        len(candidate_sets),
                        json.dumps(candidate_sets[0]),
                        json.dumps(candidate_sets),
                    ),
                ):
                    match = matches.setdefault(
                        row["occurrence_id"],
                        {"exact": False, "lemma": False, "matched_lemma": None},
                    )
                    match["lemma"] = True
                    if match["matched_lemma"] is None:
                        match["matched_lemma"] = row["normalized"]
            totals = {
                "exact": sum(m["exact"] for m in matches.values()),
                "lemma": sum(m["lemma"] for m in matches.values()),
                "auto": len(matches),
            }
            selected_ids = [
                key for key, value in matches.items() if match_mode == "auto" or value[match_mode]
            ]
            rows = connection.execute(
                """
                SELECT
                    o.occurrence_id, o.accent_key, o.n, o.token_start, o.char_start, o.char_end,
                    o.token_count AS occurrence_token_count,
                    o.surface, s.segment_id, s.text, s.start, s.end, s.clip_start, s.clip_end,
                    s.segments_json, s.analysis_json, s.boundary_reason, s.boundary_confidence, s.quality_score,
                    s.token_count, s.track_id, v.video_key, v.provider, v.video_id, v.url, v.title,
                    v.channel_id, v.channel, v.varieties_json, v.speech_style_json, v.duration,
                    v.thumbnail, t.caption_kind, t.caption_language
                FROM occurrences o
                JOIN segments s ON s.segment_id = o.segment_id
                JOIN transcripts t ON t.track_id = s.track_id
                JOIN videos v ON v.video_key = s.video_key
                WHERE o.occurrence_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(selected_ids),),
            ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            match = matches[row["occurrence_id"]]
            stored_analysis = json.loads(row["analysis_json"])
            matched_tokens = [
                token
                for token in stored_analysis["tokens"]
                if token["start"] >= row["char_start"] and token["end"] <= row["char_end"]
            ]
            matched_lemma = match["matched_lemma"]
            if matched_lemma is None and matched_tokens and all(t["lemma"] for t in matched_tokens):
                matched_lemma = " ".join(t["lemma"] for t in matched_tokens)
            accent_exact = row["accent_key"] == query_accent_key
            center = (row["token_start"] + row["n"] / 2) / max(row["occurrence_token_count"], 1)
            center_bonus = 0.03 * max(0.0, 1 - abs(0.5 - center) * 2)
            score = min(
                0.99, 0.9 * row["quality_score"] + (0.04 if accent_exact else 0) + center_bonus
            )
            candidates.append(
                {
                    "occurrence_id": row["occurrence_id"],
                    "segment_id": row["segment_id"],
                    "source_language": source_language,
                    "sentence": row["text"],
                    "match_type": "exact" if match["exact"] else "lemma",
                    "matched_surface": row["surface"],
                    "matched_lemma": matched_lemma,
                    "token_analysis": matched_tokens,
                    "analyzer": stored_analysis["analyzer"],
                    "match": {
                        "text": row["surface"],
                        "char_start": row["char_start"],
                        "char_end": row["char_end"],
                        "accent_exact": accent_exact,
                    },
                    "sentence_start": row["start"],
                    "sentence_end": row["end"],
                    "clip_start": row["clip_start"],
                    "clip_end": row["clip_end"],
                    "segments": json.loads(row["segments_json"]),
                    "boundary": {
                        "reason": row["boundary_reason"],
                        "confidence": row["boundary_confidence"],
                    },
                    "quality_score": round(score, 4),
                    "video": {
                        "video_key": row["video_key"],
                        "provider": row["provider"],
                        "id": row["video_id"],
                        "url": row["url"],
                        "title": row["title"],
                        "channel_id": row["channel_id"],
                        "channel": row["channel"],
                        "source_language": source_language,
                        "varieties": json.loads(row["varieties_json"]),
                        "speech_style": json.loads(row["speech_style_json"]),
                        "duration": row["duration"],
                        "thumbnail": row["thumbnail"],
                        "track_id": row["track_id"],
                        "caption_kind": row["caption_kind"],
                        "caption_language": row["caption_language"],
                    },
                }
            )
        candidates.sort(
            key=lambda item: (
                item["match_type"] != "exact",
                -item["quality_score"],
                item["video"]["video_key"],
                item["clip_start"],
                item["match"]["char_start"],
                item["occurrence_id"],
            )
        )
        # Keep the strongest evidence in each sentence, then diversify within match tiers.
        seen_segments: set[str] = set()
        unique = []
        for candidate in candidates:
            if candidate["segment_id"] not in seen_segments:
                unique.append(candidate)
                seen_segments.add(candidate["segment_id"])
        results = self._diversify([c for c in unique if c["match_type"] == "exact"], limit)
        if len(results) < limit:
            results += self._diversify(
                [c for c in unique if c["match_type"] == "lemma"], limit - len(results)
            )
        return {
            "query": query.strip(),
            "normalized_query": normalized,
            "source_language": source_language,
            "match_mode": match_mode,
            "morphology_available": morphology_available,
            "morphology_unavailable_reason": reason or analyzer.unavailable_reason,
            "query_analyses": query_analyses,
            "query_analyzer": analysis.provenance.as_dict(),
            "totals_by_mode": totals,
            "total_occurrences": totals[match_mode],
            "returned": len(results),
            "results": results,
        }

    @staticmethod
    def _diversify(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for allowed_per_video in range(1, limit + 1):
            video_counts: dict[str, int] = {}
            for item in selected:
                key = item["video"]["video_key"]
                video_counts[key] = video_counts.get(key, 0) + 1
            for candidate in candidates:
                if candidate["occurrence_id"] in selected_ids:
                    continue
                key = candidate["video"]["video_key"]
                if video_counts.get(key, 0) >= allowed_per_video:
                    continue
                selected.append(candidate)
                selected_ids.add(candidate["occurrence_id"])
                video_counts[key] = video_counts.get(key, 0) + 1
                if len(selected) >= limit:
                    return selected
        return selected

    def suggestions(self, *, source_language: str, limit: int = 12) -> list[dict[str, Any]]:
        source_language = self._source_language(source_language)
        limit = max(1, min(int(limit), 30))
        with closing(self._connect()) as connection:
            self._require_indexed(connection, source_language)
            rows = connection.execute(
                """
                SELECT normalized, n, surface, total_count, video_count
                FROM ngram_stats
                WHERE source_language = ? AND n BETWEEN 1 AND 3
                ORDER BY video_count DESC, total_count DESC, n ASC
                LIMIT 1000
                """,
                (source_language,),
            ).fetchall()
        words: list[dict[str, Any]] = []
        phrases: list[dict[str, Any]] = []
        for row in rows:
            terms = row["normalized"].split()
            if not terms or all(
                term in stopwords(source_language) or term.isnumeric() for term in terms
            ):
                continue
            item = {
                "source_language": source_language,
                "text": row["surface"].casefold(),
                "normalized": row["normalized"],
                "size": row["n"],
                "occurrences": row["total_count"],
                "videos": row["video_count"],
            }
            (words if row["n"] == 1 else phrases).append(item)
        target_words = math.ceil(limit / 2)
        selected = words[:target_words] + phrases[: limit - target_words]
        if len(selected) < limit:
            used = {(item["normalized"], item["size"]) for item in selected}
            for item in words[target_words:] + phrases[limit - target_words :]:
                key = (item["normalized"], item["size"])
                if key not in used:
                    selected.append(item)
                    used.add(key)
                if len(selected) == limit:
                    break
        return selected

    def status(self) -> dict[str, Any]:
        catalogues = load_catalogue_directory(self.catalogue_dir)
        configured = {catalogue.language: catalogue for catalogue in catalogues}
        enabled_languages = sorted(
            catalogue.language for catalogue in catalogues if catalogue.enabled_channels
        )
        with closing(self._connect()) as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            analyzer_rows = {
                row["source_language"]: row for row in connection.execute("SELECT * FROM analyzers")
            }
            count_rows = connection.execute(
                """
                SELECT source_language, 'videos' AS kind, COUNT(*) AS count FROM videos
                GROUP BY source_language
                UNION ALL
                SELECT source_language, 'segments', COUNT(*) FROM segments GROUP BY source_language
                UNION ALL
                SELECT source_language, 'occurrences', COUNT(*) FROM occurrences
                GROUP BY source_language
                """
            ).fetchall()
            caption_rows = connection.execute(
                """
                SELECT source_language, caption_kind, COUNT(*) AS count
                FROM transcripts GROUP BY source_language, caption_kind
                """
            ).fetchall()
        indexed_languages = sorted(
            {row["source_language"] for row in count_rows if row["kind"] == "videos"}
        )
        counts: dict[str, dict[str, int]] = {}
        for row in count_rows:
            counts.setdefault(row["source_language"], {})[row["kind"]] = row["count"]
        captions: dict[str, dict[str, int]] = {}
        for row in caption_rows:
            captions.setdefault(row["source_language"], {})[row["caption_kind"]] = row["count"]
        languages = []
        for language in sorted(set(configured) | set(indexed_languages)):
            catalogue = configured.get(language)
            language_counts = counts.get(language, {})
            languages.append(
                {
                    "source_language": language,
                    "configured": catalogue is not None,
                    "enabled": bool(catalogue and catalogue.enabled_channels),
                    "indexed": language in indexed_languages,
                    "configured_channels": len(catalogue.channels) if catalogue else 0,
                    "enabled_channels": len(catalogue.enabled_channels) if catalogue else 0,
                    "videos": language_counts.get("videos", 0),
                    "segments": language_counts.get("segments", 0),
                    "occurrences": language_counts.get("occurrences", 0),
                    "caption_kinds": captions.get(language, {}),
                    "analyzer_id": json.loads(analyzer_rows[language]["provenance_json"])[
                        "identity"
                    ]
                    if language in analyzer_rows
                    else None,
                    "analyzer": json.loads(analyzer_rows[language]["provenance_json"])
                    if language in analyzer_rows
                    else None,
                    "morphology_available": bool(analyzer_rows[language]["morphology_available"])
                    if language in analyzer_rows
                    else False,
                }
            )
        totals = {
            kind: sum(item.get(kind, 0) for item in counts.values())
            for kind in ("videos", "segments", "occurrences")
        }
        aggregate_captions: dict[str, int] = {}
        for items in captions.values():
            for kind, count in items.items():
                aggregate_captions[kind] = aggregate_captions.get(kind, 0) + count
        return {
            "ready": True,
            "package_version": meta.get("package_version"),
            "database_schema_version": int(meta["schema_version"]),
            "built_at": meta.get("built_at"),
            "max_ngram": int(meta.get("max_ngram", 0)),
            "analyzer_id": meta.get("analyzer_id"),
            "configured_languages": sorted(configured),
            "enabled_languages": enabled_languages,
            "indexed_languages": indexed_languages,
            "languages": languages,
            "videos": totals["videos"],
            "segments": totals["segments"],
            "occurrences": totals["occurrences"],
            "caption_kinds": dict(sorted(aggregate_captions.items())),
        }
