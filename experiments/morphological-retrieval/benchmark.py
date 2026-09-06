"""Measure the two no-model analyzers on a temporary copy of the local Spanish seed.

Run from the repository root. No raw captions or existing indexes are modified.
The legacy adapter is confined to this experiment; production indexing accepts
only the versioned cache contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from speech_retrieval.analysis import clear_analyzer_cache, get_analyzer
from speech_retrieval.identity import (
    CACHE_SCHEMA_VERSION,
    DATABASE_SCHEMA_VERSION,
    track_id,
    video_key,
)
from speech_retrieval.indexing import build_index
from speech_retrieval.search import Corpus
from speech_retrieval.text import tokens_with_spans

QUERIES = ("casa", "casas", "estar", "estoy", "estaba", "la verdad", "bonitas", "que")


def measure_query(corpus: Corpus, query: str, mode: str, repeats: int) -> dict:
    response = corpus.search(query, source_language="es", match_mode=mode)
    elapsed = []
    for _ in range(repeats):
        start = time.perf_counter()
        corpus.search(query, source_language="es", match_mode=mode)
        elapsed.append((time.perf_counter() - start) * 1000)
    elapsed.sort()
    return {
        "median_ms": round(statistics.median(elapsed), 3),
        "p95_ms": round(elapsed[max(0, int(len(elapsed) * 0.95) - 1)], 3),
        "occurrences": response["total_occurrences"],
    }


def prefix_terms(root: Path) -> list[str]:
    segments = root / "derived" / "corpora" / "es" / "segments.jsonl"
    for line in segments.read_text().splitlines():
        tokens = tokens_with_spans(json.loads(line)["text"])
        if len(tokens) >= 5:
            return [token.text for token in tokens[:5]]
    raise ValueError("No five-token segment in benchmark corpus")


def prepare_seed(source: Path, target: Path) -> dict:
    versioned = source / "raw" / "corpora" / "es"
    if versioned.exists():
        shutil.copytree(versioned, target / "raw" / "corpora" / "es")
    else:
        for path in sorted((source / "raw" / "videos").glob("*/metadata.json")):
            metadata = json.loads(path.read_text())
            captions = path.with_name("subtitles.raw.json3").read_bytes()
            key = video_key(metadata["provider"], "es", metadata["video_id"])
            track = track_id(key, metadata["caption_kind"], metadata["caption_language"])
            metadata.update(
                {
                    "cache_schema_version": CACHE_SCHEMA_VERSION,
                    "catalogue_schema_version": 1,
                    "catalogue_id": "es",
                    "source_language": "es",
                    "video_key": key,
                    "track_id": track,
                    "content_sha256": hashlib.sha256(captions).hexdigest(),
                }
            )
            destination = target / "raw" / "corpora" / "es" / key / track
            destination.mkdir(parents=True)
            (destination / "metadata.json").write_text(json.dumps(metadata))
            (destination / "subtitles.raw.json3").write_bytes(captions)
    raw_files = sorted((target / "raw" / "corpora" / "es").glob("*/*/subtitles.raw.json3"))
    digest = hashlib.sha256()
    for path in raw_files:
        digest.update(path.read_bytes())
    if not raw_files:
        raise ValueError("No cached Spanish seed found")
    return {"transcripts": len(raw_files), "captions_sha256": digest.hexdigest()}


def benchmark(data_dir: Path, repeats: int) -> dict:
    report: dict = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "query_repeats": repeats,
        "analyzers": {},
    }
    with tempfile.TemporaryDirectory(prefix="morphology-benchmark-") as temporary:
        root = Path(temporary)
        report["seed"] = prepare_seed(data_dir, root)
        for selection in ("unicode", "simplemma"):
            clear_analyzer_cache()
            start = time.perf_counter()
            analyzer = get_analyzer("es", selection, str((root / "models" / "stanza").resolve()))
            analyzer.analyze("Las casas bonitas están aquí.")
            initialization_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            built = build_index(data_dir=root, analyzer=selection)
            build_seconds = time.perf_counter() - start
            corpus = Corpus(root)
            queries = {}
            for mode in ("exact", "auto"):
                for query in QUERIES:
                    queries[f"{mode}:{query}"] = measure_query(corpus, query, mode, repeats)
            terms = prefix_terms(root)
            ngram_timings = {}
            modes = ("exact", "lemma") if analyzer.morphology_available else ("exact",)
            for mode in modes:
                for size in range(1, 6):
                    ngram_timings[f"{mode}:{size}"] = measure_query(
                        corpus, " ".join(terms[:size]), mode, repeats
                    )
            report["analyzers"][selection] = {
                "provenance": analyzer.provenance.as_dict(),
                "initialization_ms": round(initialization_ms, 3),
                "build_seconds": round(build_seconds, 3),
                "index_bytes": corpus.database.stat().st_size,
                "segments": built["segment_count"],
                "occurrences": built["occurrence_count"],
                "queries": queries,
                "ngram_prefix_timings": ngram_timings,
            }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    result = json.dumps(benchmark(args.data_dir, args.repeats), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")
