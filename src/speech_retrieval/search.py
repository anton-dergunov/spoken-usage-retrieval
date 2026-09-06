from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis import (
    AnalyzedToken,
    IncompatibleAnalyzerError,
    UnsupportedAnalysisError,
    recorded_analyzer,
)
from .catalogue import canonical_language, load_catalogue_directory
from .contracts import (
    Clip,
    CorpusStatistics,
    CorpusStatus,
    SearchResponse,
    Suggestion,
)
from .identity import DATABASE_SCHEMA_VERSION
from .service import activity_is_alive, read_update_state
from .settings import Settings
from .stopwords import stopwords
from .text import accent_key, normalized_query


class SearchError(ValueError):
    pass


class IncompatibleIndexError(RuntimeError):
    pass


class Corpus:
    def __init__(
        self,
        settings: Settings | str | Path,
        catalogue_dir: str | Path = "config/channels",
        *,
        models_dir: str | Path | None = None,
    ):
        if isinstance(settings, Settings):
            self.settings = settings
        else:
            self.settings = Settings(
                data_dir=Path(settings),
                catalogue_dir=Path(catalogue_dir),
                models_dir=Path(models_dir) if models_dir is not None else None,
            )
        self.data_dir = self.settings.data_dir
        self.catalogue_dir = self.settings.catalogue_dir
        self.models_dir = self.settings.resolved_models_dir
        self.database = self.data_dir / "index" / "corpus.sqlite3"
        self._closed = False

    def __enter__(self) -> Corpus:
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Corpus is closed")

    def _connect(self) -> sqlite3.Connection:
        self._ensure_open()
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

    def check_ready(self) -> None:
        """Validate that the index and every recorded query analyzer are usable."""
        load_catalogue_directory(self.catalogue_dir)
        with closing(self._connect()) as connection:
            records = connection.execute(
                "SELECT provenance_json FROM analyzers ORDER BY source_language"
            ).fetchall()
        if not records:
            raise IncompatibleIndexError("search index contains no configured analyzers")
        for record in records:
            try:
                recorded_analyzer(json.loads(record["provenance_json"]), self.models_dir)
            except (UnsupportedAnalysisError, IncompatibleAnalyzerError) as error:
                raise IncompatibleIndexError(str(error)) from error

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

    @staticmethod
    def _sequence_matches(
        connection: sqlite3.Connection,
        source_language: str,
        kind: str,
        candidate_sets: list[list[str]],
        max_ngram: int,
    ) -> list[sqlite3.Row]:
        if not candidate_sets or not all(candidate_sets) or len(candidate_sets) > max_ngram:
            return []
        candidates_json = json.dumps(candidate_sets)
        return connection.execute(
            """
            SELECT streams.segment_id, first.stream_id, first.position AS token_start,
                   first.char_start, last.char_end, streams.token_count,
                   (
                       SELECT group_concat(ordered.normalized, ' ')
                       FROM (
                           SELECT matched.normalized
                           FROM stream_tokens matched
                           WHERE matched.stream_id = first.stream_id
                             AND matched.position BETWEEN first.position
                                 AND first.position + ? - 1
                           ORDER BY matched.position
                       ) ordered
                   ) AS matched_normalized
            FROM stream_tokens first
            JOIN token_streams streams ON streams.stream_id = first.stream_id
            JOIN stream_tokens last
              ON last.stream_id = first.stream_id
             AND last.position = first.position + ? - 1
            WHERE first.source_language = ? AND first.kind = ?
              AND first.normalized IN (SELECT value FROM json_each(?))
              AND NOT EXISTS (
                  SELECT 1 FROM json_each(?) candidate_position
                  WHERE NOT EXISTS (
                      SELECT 1
                      FROM stream_tokens candidate_token
                      JOIN json_each(candidate_position.value) allowed
                        ON allowed.value = candidate_token.normalized
                      WHERE candidate_token.stream_id = first.stream_id
                        AND candidate_token.position = first.position
                            + CAST(candidate_position.key AS INTEGER)
                  )
              )
            ORDER BY streams.segment_id,
                     CASE WHEN first.stream_id LIKE '%:unicode' THEN 0 ELSE 1 END,
                     first.position, first.stream_id
            """,
            (
                len(candidate_sets),
                len(candidate_sets),
                source_language,
                kind,
                json.dumps(candidate_sets[0]),
                candidates_json,
            ),
        ).fetchall()

    def search(
        self,
        query: str,
        *,
        source_language: str,
        match_mode: str = "auto",
        order: str = "ranked",
        limit: int = 20,
        seed: int | None = None,
    ) -> SearchResponse:
        source_language = self._source_language(source_language)
        if match_mode not in ("exact", "lemma", "auto"):
            raise SearchError("match_mode must be exact, lemma, or auto")
        if order not in ("ranked", "random"):
            raise SearchError("order must be ranked or random")
        if order == "ranked" and seed is not None:
            raise SearchError("seed is only valid with random order")
        if seed is not None and not -(2**63) <= seed < 2**63:
            raise SearchError("seed must be a signed 64-bit integer")
        if len(query) > self.settings.max_query_length:
            raise SearchError(f"query must be at most {self.settings.max_query_length} characters")
        normalized, query_tokens = normalized_query(query)
        if not query_tokens:
            raise SearchError("Enter a word or phrase to search for.")
        if len(query_tokens) > 5:
            raise SearchError("This prototype searches phrases of up to five words.")
        if not 1 <= int(limit) <= self.settings.max_search_limit:
            raise SearchError(f"limit must be between 1 and {self.settings.max_search_limit}")
        limit = int(limit)
        query_accent_key = accent_key(query)
        with closing(self._connect()) as connection:
            self._require_indexed(connection, source_language)
            max_ngram = int(
                connection.execute("SELECT value FROM meta WHERE key = 'max_ngram'").fetchone()[0]
            )
            metadata = connection.execute(
                "SELECT * FROM analyzers WHERE source_language = ?", (source_language,)
            ).fetchone()
            provenance = json.loads(metadata["provenance_json"])
            reason = metadata["unavailable_reason"]
            try:
                analyzer = recorded_analyzer(provenance, self.models_dir)
            except UnsupportedAnalysisError as error:
                analyzer_name = provenance.get("name", "recorded analyzer")
                raise IncompatibleIndexError(
                    f"Index for {source_language} was built with {analyzer_name}, but that "
                    f"analyzer is unavailable: {error}. Restore the recorded analyzer or rebuild "
                    "with --analyzer unicode, simplemma, or stanza."
                ) from error
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

            def add_matches(rows: list[sqlite3.Row], kind: str, size: int) -> None:
                for row in rows:
                    occurrence_id = f"{row['segment_id']}:{row['char_start']}:{row['char_end']}"
                    priority = (
                        0 if row["stream_id"].endswith(":unicode") else 1 if kind == "exact" else 2
                    )
                    match = matches.get(occurrence_id)
                    if match is None:
                        match = {
                            "exact": False,
                            "lemma": False,
                            "matched_lemma": None,
                            "segment_id": row["segment_id"],
                            "char_start": row["char_start"],
                            "char_end": row["char_end"],
                            "token_start": row["token_start"],
                            "n": size,
                            "token_count": row["token_count"],
                            "stream_priority": priority,
                        }
                        matches[occurrence_id] = match
                    elif priority < match["stream_priority"]:
                        match.update(
                            token_start=row["token_start"],
                            n=size,
                            token_count=row["token_count"],
                            stream_priority=priority,
                        )
                    match[kind] = True
                    if kind == "lemma" and match["matched_lemma"] is None:
                        match["matched_lemma"] = row["matched_normalized"]

            for key, size in sorted(surface_keys):
                rows = self._sequence_matches(
                    connection,
                    source_language,
                    "exact",
                    [[part] for part in key.split(" ")],
                    max_ngram,
                )
                add_matches(rows, "exact", size)
            if morphology_available and candidate_sets and all(candidate_sets):
                # Index the first position, then check remaining candidate sets in SQL.
                # JSON arrays avoid SQLite's variable limit and Cartesian expansion.
                lemma_rows = self._sequence_matches(
                    connection, source_language, "lemma", candidate_sets, max_ngram
                )
                add_matches(lemma_rows, "lemma", len(candidate_sets))
            totals = {
                "exact": sum(m["exact"] for m in matches.values()),
                "lemma": sum(m["lemma"] for m in matches.values()),
                "auto": len(matches),
            }
            selected_ids = [
                key for key, value in matches.items() if match_mode == "auto" or value[match_mode]
            ]
            segment_ids = sorted({matches[item]["segment_id"] for item in selected_ids})
            rows = connection.execute(
                """
                SELECT s.segment_id, s.text, s.clip_start, s.quality_score, v.video_key
                FROM segments s
                JOIN videos v ON v.video_key = s.video_key
                WHERE s.segment_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(segment_ids),),
            ).fetchall()

        segment_rows = {row["segment_id"]: row for row in rows}
        candidates: list[dict[str, Any]] = []
        for occurrence_id in selected_ids:
            match = matches[occurrence_id]
            row = segment_rows[match["segment_id"]]
            surface = row["text"][match["char_start"] : match["char_end"]]
            accent_exact = accent_key(surface) == query_accent_key
            center = (match["token_start"] + match["n"] / 2) / max(match["token_count"], 1)
            center_bonus = 0.03 * max(0.0, 1 - abs(0.5 - center) * 2)
            score = min(
                0.99, 0.9 * row["quality_score"] + (0.04 if accent_exact else 0) + center_bonus
            )
            candidates.append(
                {
                    "occurrence_id": occurrence_id,
                    "segment_id": row["segment_id"],
                    "match_type": "exact" if match["exact"] else "lemma",
                    "matched_lemma": match["matched_lemma"],
                    "char_start": match["char_start"],
                    "char_end": match["char_end"],
                    "accent_exact": accent_exact,
                    "clip_start": row["clip_start"],
                    "quality_score": round(score, 4),
                    "video_key": row["video_key"],
                }
            )
        candidates.sort(
            key=lambda item: (
                item["match_type"] != "exact",
                -item["quality_score"],
                item["video_key"],
                item["clip_start"],
                item["char_start"],
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
        effective_seed = seed
        if order == "random":
            if effective_seed is None:
                effective_seed = secrets.randbits(63)
            unique.sort(
                key=lambda item: hashlib.sha256(
                    f"{effective_seed}:{item['occurrence_id']}".encode()
                ).digest()
            )
            selected = unique[:limit]
        else:
            selected = self._diversify([c for c in unique if c["match_type"] == "exact"], limit)
            if len(selected) < limit:
                selected += self._diversify(
                    [c for c in unique if c["match_type"] == "lemma"],
                    limit - len(selected),
                )
        selected_segment_ids = [item["segment_id"] for item in selected]
        with closing(self._connect()) as connection:
            full_rows = connection.execute(
                """
                SELECT
                    s.segment_id, s.text, s.start, s.end, s.clip_start, s.clip_end,
                    s.segments_json, s.analysis_json, s.boundary_reason,
                    s.boundary_confidence, s.track_id, v.video_key, v.provider, v.video_id,
                    v.url, v.title, v.channel_id, v.channel, v.varieties_json,
                    v.speech_style_json, v.duration, v.thumbnail,
                    t.caption_kind, t.caption_language
                FROM segments s
                JOIN transcripts t ON t.track_id = s.track_id
                JOIN videos v ON v.video_key = s.video_key
                WHERE s.segment_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(selected_segment_ids),),
            ).fetchall()
        full_by_segment = {row["segment_id"]: row for row in full_rows}
        results: list[dict[str, Any]] = []
        for candidate in selected:
            row = full_by_segment[candidate["segment_id"]]
            stored_analysis = json.loads(row["analysis_json"])
            matched_tokens = [
                token
                for token in stored_analysis["tokens"]
                if token["start"] >= candidate["char_start"]
                and token["end"] <= candidate["char_end"]
            ]
            matched_lemma = candidate["matched_lemma"]
            if matched_lemma is None and matched_tokens and all(t["lemma"] for t in matched_tokens):
                matched_lemma = " ".join(t["lemma"] for t in matched_tokens)
            surface = row["text"][candidate["char_start"] : candidate["char_end"]]
            results.append(
                {
                    "occurrence_id": candidate["occurrence_id"],
                    "segment_id": row["segment_id"],
                    "source_language": source_language,
                    "sentence": row["text"],
                    "match_type": candidate["match_type"],
                    "matched_surface": surface,
                    "matched_lemma": matched_lemma,
                    "token_analysis": matched_tokens,
                    "analyzer": stored_analysis["analyzer"],
                    "match": {
                        "text": surface,
                        "char_start": candidate["char_start"],
                        "char_end": candidate["char_end"],
                        "accent_exact": candidate["accent_exact"],
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
                    "quality_score": candidate["quality_score"],
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
                        "caption_track_kind": (
                            "authored" if row["caption_kind"] == "manual" else "automatic"
                        ),
                        "caption_provider_track_id": row["caption_language"],
                        "caption_is_source": True,
                    },
                }
            )
        for rank, result in enumerate(results, 1):
            result["rank"] = rank
            result["score"] = result["quality_score"]
        return SearchResponse.model_validate(
            {
                "query": query.strip(),
                "normalized_query": normalized,
                "source_language": source_language,
                "match_mode": match_mode,
                "order": order,
                "seed": effective_seed,
                "morphology_available": morphology_available,
                "morphology_unavailable_reason": reason or analyzer.unavailable_reason,
                "query_analyses": query_analyses,
                "query_analyzer": analysis.provenance.as_dict(),
                "totals_by_mode": totals,
                "total_occurrences": totals[match_mode],
                "returned": len(results),
                "results": results,
            }
        )

    @staticmethod
    def _diversify(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for allowed_per_video in range(1, limit + 1):
            video_counts: dict[str, int] = {}
            for item in selected:
                key = item["video_key"]
                video_counts[key] = video_counts.get(key, 0) + 1
            for candidate in candidates:
                if candidate["occurrence_id"] in selected_ids:
                    continue
                key = candidate["video_key"]
                if video_counts.get(key, 0) >= allowed_per_video:
                    continue
                selected.append(candidate)
                selected_ids.add(candidate["occurrence_id"])
                video_counts[key] = video_counts.get(key, 0) + 1
                if len(selected) >= limit:
                    return selected
        return selected

    def suggestions(self, *, source_language: str, limit: int = 12) -> list[Suggestion]:
        source_language = self._source_language(source_language)
        if not 1 <= int(limit) <= self.settings.max_suggestion_limit:
            raise SearchError(f"limit must be between 1 and {self.settings.max_suggestion_limit}")
        limit = int(limit)
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
        return [Suggestion.model_validate(item) for item in selected]

    def status(self) -> CorpusStatus:
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
                SELECT source_language, 'occurrences', occurrence_count FROM language_stats
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
        return CorpusStatus.model_validate(
            {
                "ready": True,
                "error": None,
                "package_version": meta.get("package_version"),
                "database_schema_version": int(meta["schema_version"]),
                "built_at": meta.get("built_at"),
                "max_ngram": int(meta.get("max_ngram", 0)),
                "analyzer_selection": meta.get("analyzer_selection"),
                "analyzer_id": meta.get("analyzer_id"),
                "configured_languages": sorted(configured),
                "enabled_languages": enabled_languages,
                "indexed_languages": indexed_languages,
                "languages": languages,
                "videos": totals["videos"],
                "segments": totals["segments"],
                "occurrences": totals["occurrences"],
                "caption_kinds": dict(sorted(aggregate_captions.items())),
                "channel_mutations_enabled": self.settings.enable_channel_mutations,
            }
        )

    def clip(self, segment_id: str) -> Clip:
        if not segment_id or len(segment_id) > 200:
            raise SearchError("invalid segment ID")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    s.segment_id, s.source_language, s.text, s.start, s.end,
                    s.clip_start, s.clip_end, s.segments_json, s.analysis_json,
                    s.boundary_reason, s.boundary_confidence, s.quality_score, s.track_id,
                    v.video_key, v.provider, v.video_id, v.url, v.title, v.channel_id,
                    v.channel, v.varieties_json, v.speech_style_json, v.duration, v.thumbnail,
                    t.caption_kind, t.caption_language
                FROM segments s
                JOIN transcripts t ON t.track_id = s.track_id
                JOIN videos v ON v.video_key = s.video_key
                WHERE s.segment_id = ?
                """,
                (segment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(segment_id)
        analysis = json.loads(row["analysis_json"])
        return Clip.model_validate(
            {
                "segment_id": row["segment_id"],
                "source_language": row["source_language"],
                "source_text": row["text"],
                "sentence_start": row["start"],
                "sentence_end": row["end"],
                "clip_start": row["clip_start"],
                "clip_end": row["clip_end"],
                "segments": json.loads(row["segments_json"]),
                "boundary": {
                    "reason": row["boundary_reason"],
                    "confidence": row["boundary_confidence"],
                },
                "quality_score": row["quality_score"],
                "analyzer": analysis["analyzer"],
                "video": {
                    "video_key": row["video_key"],
                    "provider": row["provider"],
                    "id": row["video_id"],
                    "url": row["url"],
                    "title": row["title"],
                    "channel_id": row["channel_id"],
                    "channel": row["channel"],
                    "source_language": row["source_language"],
                    "varieties": json.loads(row["varieties_json"]),
                    "speech_style": json.loads(row["speech_style_json"]),
                    "duration": row["duration"],
                    "thumbnail": row["thumbnail"],
                    "track_id": row["track_id"],
                    "caption_kind": row["caption_kind"],
                    "caption_language": row["caption_language"],
                    "caption_track_kind": (
                        "authored" if row["caption_kind"] == "manual" else "automatic"
                    ),
                    "caption_provider_track_id": row["caption_language"],
                    "caption_is_source": True,
                },
            }
        )

    def statistics(self) -> CorpusStatistics:
        catalogues = load_catalogue_directory(self.catalogue_dir)
        configured = {
            (catalogue.language, channel.id): (channel.name, channel.enabled)
            for catalogue in catalogues
            for channel in catalogue.channels
        }
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT v.source_language, v.channel_config_id, MAX(v.channel) AS channel_name,
                       COUNT(DISTINCT v.video_key) AS videos,
                       COUNT(DISTINCT s.segment_id) AS segments
                FROM videos v LEFT JOIN segments s ON s.video_key = v.video_key
                GROUP BY v.source_language, v.channel_config_id
                """
            ).fetchall()
            caption_rows = connection.execute(
                """
                SELECT v.source_language, v.channel_config_id, t.caption_kind, COUNT(*) AS count
                FROM transcripts t JOIN videos v ON v.video_key = t.video_key
                GROUP BY v.source_language, v.channel_config_id, t.caption_kind
                """
            ).fetchall()
        indexed = {(row["source_language"], row["channel_config_id"]): row for row in rows}
        captions: dict[tuple[str, str], dict[str, int]] = {}
        for row in caption_rows:
            captions.setdefault((row["source_language"], row["channel_config_id"]), {})[
                row["caption_kind"]
            ] = row["count"]
        channel_items: list[dict[str, Any]] = []
        for key in sorted(set(configured) | set(indexed)):
            configured_item = configured.get(key)
            indexed_item = indexed.get(key)
            if configured_item is None:
                assert indexed_item is not None
                channel_name = str(indexed_item["channel_name"])
            else:
                channel_name = configured_item[0]
            channel_items.append(
                {
                    "source_language": key[0],
                    "channel_id": key[1],
                    "channel_name": channel_name,
                    "configured": configured_item is not None,
                    "enabled": configured_item[1] if configured_item else False,
                    "videos": indexed_item["videos"] if indexed_item else 0,
                    "segments": indexed_item["segments"] if indexed_item else 0,
                    "caption_kinds": captions.get(key, {}),
                }
            )
        language_items: list[dict[str, Any]] = []
        for language in sorted({item["source_language"] for item in channel_items}):
            selected = [item for item in channel_items if item["source_language"] == language]
            language_captions: dict[str, int] = {}
            for item in selected:
                for kind, count in item["caption_kinds"].items():
                    language_captions[kind] = language_captions.get(kind, 0) + count
            language_items.append(
                {
                    "source_language": language,
                    "videos": sum(item["videos"] for item in selected),
                    "segments": sum(item["segments"] for item in selected),
                    "caption_kinds": language_captions,
                }
            )
        aggregate_captions: dict[str, int] = {}
        for item in language_items:
            for kind, count in item["caption_kinds"].items():
                aggregate_captions[kind] = aggregate_captions.get(kind, 0) + count
        state = read_update_state(self.data_dir / "reports" / "update-state.json")
        current_activity = state.get("current_activity")
        if not activity_is_alive(current_activity):
            current_activity = None
        return CorpusStatistics.model_validate(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "videos": sum(item["videos"] for item in language_items),
                "segments": sum(item["segments"] for item in language_items),
                "caption_kinds": aggregate_captions,
                "languages": language_items,
                "channels": channel_items,
                "last_successful_update": state.get("last_successful_update"),
                "current_activity": current_activity,
                "recent_failures": state.get("recent_failures", []),
            }
        )
