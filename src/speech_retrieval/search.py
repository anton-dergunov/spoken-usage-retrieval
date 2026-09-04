from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from .text import accent_key, normalized_query

SPANISH_STOPWORDS = {
    "a", "al", "algo", "como", "con", "contra", "cual", "cuando", "de", "del", "desde",
    "donde", "el", "ella", "ellos", "en", "era", "es", "esa", "ese", "eso", "esta", "este",
    "esto", "fue", "ha", "hay", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi",
    "muy", "no", "nos", "o", "para", "pero", "por", "porque", "que", "se", "si", "sin",
    "sobre", "su", "sus", "te", "tiene", "todo", "tu", "un", "una", "uno", "unos", "y", "ya",
    "yo",
}


class SearchError(ValueError):
    pass


class Corpus:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.database = self.data_dir / "index" / "corpus.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        if not self.database.exists():
            raise FileNotFoundError(f"search index not found: {self.database}")
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        normalized, query_tokens = normalized_query(query)
        if not query_tokens:
            raise SearchError("Enter a word or phrase to search for.")
        if len(query_tokens) > 5:
            raise SearchError("This prototype searches phrases of up to five words.")
        limit = max(1, min(int(limit), 50))
        query_accent_key = accent_key(query)
        with self._connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM occurrences WHERE normalized = ? AND n = ?",
                (normalized, len(query_tokens)),
            ).fetchone()[0]
            rows = connection.execute(
                """
                SELECT o.*, s.*, v.*
                FROM occurrences o
                JOIN segments s ON s.segment_id = o.segment_id
                JOIN videos v ON v.video_id = s.video_id
                WHERE o.normalized = ? AND o.n = ?
                ORDER BY s.quality_score DESC, s.video_id, s.start
                LIMIT ?
                """,
                (normalized, len(query_tokens), min(1000, max(100, limit * 20))),
            ).fetchall()

        candidates: list[dict[str, Any]] = []
        seen_segments: set[str] = set()
        for row in rows:
            if row["segment_id"] in seen_segments:
                continue
            seen_segments.add(row["segment_id"])
            accent_exact = row["accent_key"] == query_accent_key
            center = (row["token_start"] + row["n"] / 2) / max(row["token_count"], 1)
            center_bonus = 0.03 * max(0.0, 1 - abs(0.5 - center) * 2)
            score = min(0.99, 0.9 * row["quality_score"] + (0.04 if accent_exact else 0) + center_bonus)
            candidates.append(
                {
                    "occurrence_id": row["occurrence_id"],
                    "sentence": row["text"],
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
                    "boundary": {
                        "reason": row["boundary_reason"],
                        "confidence": row["boundary_confidence"],
                    },
                    "quality_score": round(score, 4),
                    "video": {
                        "provider": row["provider"],
                        "id": row["video_id"],
                        "url": row["url"],
                        "title": row["title"],
                        "channel_id": row["channel_id"],
                        "channel": row["channel"],
                        "varieties": json.loads(row["varieties_json"]),
                        "speech_style": json.loads(row["speech_style_json"]),
                        "duration": row["duration"],
                        "thumbnail": row["thumbnail"],
                        "caption_kind": row["caption_kind"],
                        "caption_language": row["caption_language"],
                    },
                }
            )
        candidates.sort(key=lambda item: (-item["quality_score"], item["video"]["id"], item["clip_start"]))
        results = self._diversify(candidates, limit)
        return {
            "query": query.strip(),
            "normalized_query": normalized,
            "total_occurrences": total,
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
                video_id = item["video"]["id"]
                video_counts[video_id] = video_counts.get(video_id, 0) + 1
            for candidate in candidates:
                if candidate["occurrence_id"] in selected_ids:
                    continue
                video_id = candidate["video"]["id"]
                if video_counts.get(video_id, 0) >= allowed_per_video:
                    continue
                selected.append(candidate)
                selected_ids.add(candidate["occurrence_id"])
                video_counts[video_id] = video_counts.get(video_id, 0) + 1
                if len(selected) >= limit:
                    return selected
        return selected

    def suggestions(self, limit: int = 12) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 30))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT normalized, n, surface, total_count, video_count
                FROM ngram_stats
                WHERE n BETWEEN 1 AND 3
                ORDER BY video_count DESC, total_count DESC, n ASC
                LIMIT 1000
                """
            ).fetchall()
        words: list[dict[str, Any]] = []
        phrases: list[dict[str, Any]] = []
        for row in rows:
            terms = row["normalized"].split()
            content_terms = [term for term in terms if term not in SPANISH_STOPWORDS and len(term) > 2]
            if not content_terms:
                continue
            item = {
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
        with self._connect() as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            videos = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            segments = connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
            occurrences = connection.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
            caption_rows = connection.execute(
                "SELECT caption_kind, COUNT(*) AS count FROM videos GROUP BY caption_kind"
            ).fetchall()
        return {
            "ready": True,
            "version": meta.get("version"),
            "built_at": meta.get("built_at"),
            "max_ngram": int(meta.get("max_ngram", 0)),
            "videos": videos,
            "segments": segments,
            "occurrences": occurrences,
            "caption_kinds": {row["caption_kind"]: row["count"] for row in caption_rows},
        }
