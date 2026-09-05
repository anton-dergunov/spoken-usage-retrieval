#!/usr/bin/env python3
"""Prepare, validate, and run the ten-language morphology experiment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from compact_index import (
    auto_search,
    build_database,
    build_streams,
    occurrence_id,
    search_database,
    verify_parity,
    warm_query_timings,
)
from morphology_experiment import (
    Query,
    build_training_lexicon,
    dataclass_rows,
    production_key,
    read_conllu,
    result_document,
    score_analysis,
    select_queries,
    strict_key,
)

from speech_retrieval.analysis import (
    Analysis,
    InvalidAnalysisError,
    UnsupportedAnalysisError,
    _stanza_resources,
    download_models,
    get_analyzer,
)
from speech_retrieval.text import tokens_with_spans

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
DEFAULT_DATA_ROOT = REPOSITORY / "data/experiments/morphological-retrieval-multilingual"
DEFAULT_RESULTS = HERE / "results.json"
DEFAULT_REPORT = HERE / "README.md"


def load_config() -> dict[str, Any]:
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_identity(config: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    for filename in ("run.py", "morphology_experiment.py", "compact_index.py"):
        digest.update((HERE / filename).read_bytes())
    return digest.hexdigest()


def treebank_root(data_root: Path, config: dict[str, Any]) -> Path:
    return data_root / "ud" / f"ud-treebanks-v{config['ud']['release']}"


def treebank_paths(
    data_root: Path, config: dict[str, Any], language: dict[str, Any]
) -> dict[str, Path]:
    root = treebank_root(data_root, config) / language["treebank"]
    return {
        split: root / f"{language['prefix']}-ud-{split}.conllu"
        for split in ("train", "dev", "test")
    }


def input_manifest(
    data_root: Path, config: dict[str, Any], language: dict[str, Any]
) -> dict[str, Any]:
    root = treebank_root(data_root, config) / language["treebank"]
    paths = treebank_paths(data_root, config, language)
    license_line = None
    readme = root / "README.md"
    if readme.exists():
        license_line = next(
            (
                line.removeprefix("License:").strip()
                for line in readme.read_text().splitlines()
                if line.startswith("License:")
            ),
            None,
        )
    files = {
        split: {"path": str(path), "sha256": sha256(path)}
        for split, path in paths.items()
        if path.exists()
    }
    for filename in ("README.md", "LICENSE.txt", "LICENSE"):
        path = root / filename
        if path.exists():
            files[filename] = {"path": str(path), "sha256": sha256(path)}
    return {
        "treebank": language["treebank"],
        "declared_license": language["license"],
        "recorded_license": license_line,
        "license_matches": license_line == language["license"],
        "files": files,
    }


def package_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for package, expected in config["packages"].items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        rows.append(
            {
                "package": package,
                "expected": expected,
                "installed": installed,
                "ready": installed == expected,
            }
        )
    return rows


def preflight(data_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    archive = data_root / "ud" / config["ud"]["archive_filename"]
    archive_hash = sha256(archive) if archive.exists() else None
    languages = []
    models_dir = data_root / "stanza"
    for language in config["languages"]:
        paths = treebank_paths(data_root, config, language)
        missing = [split for split, path in paths.items() if not path.exists()]
        model_status = "ready"
        model_reason = None
        try:
            stanza_key, resources, processors = _stanza_resources(models_dir, language["tag"])
            required = [
                models_dir / stanza_key / processor / f"{package}.pt"
                for processor, package in processors.items()
            ]
            for processor, package in processors.items():
                model = resources.get(processor, {}).get(package, {})
                required.extend(
                    models_dir / stanza_key / dependency["model"] / f"{dependency['package']}.pt"
                    for dependency in model.get("dependencies", [])
                )
            missing_models = [str(path) for path in required if not path.exists()]
            if missing_models:
                raise UnsupportedAnalysisError(
                    "Missing Stanza model files: " + ", ".join(missing_models)
                )
        except UnsupportedAnalysisError as error:
            model_status, model_reason = "missing", str(error)
        manifest = input_manifest(data_root, config, language) if not missing else None
        languages.append(
            {
                "language": language["tag"],
                "treebank": language["treebank"],
                "missing_splits": missing,
                "license": manifest["recorded_license"] if manifest else language["license"],
                "license_matches": manifest["license_matches"] if manifest else None,
                "stanza_model": model_status,
                "stanza_reason": model_reason,
            }
        )
    return {
        "ready": (
            archive_hash == config["ud"]["archive_sha256"]
            and all(row["ready"] for row in package_status(config))
            and all(
                not row["missing_splits"]
                and row["license_matches"] is not False
                and row["stanza_model"] == "ready"
                for row in languages
            )
        ),
        "archive": {
            "path": str(archive),
            "expected_sha256": config["ud"]["archive_sha256"],
            "actual_sha256": archive_hash,
            "ready": archive_hash == config["ud"]["archive_sha256"],
        },
        "packages": package_status(config),
        "languages": languages,
    }


def prepare_ud(data_root: Path, config: dict[str, Any]) -> None:
    destination = data_root / "ud" / config["ud"]["archive_filename"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        print(f"Downloading {config['ud']['archive_url']} -> {destination}")
        urllib.request.urlretrieve(config["ud"]["archive_url"], destination)  # noqa: S310
    actual = sha256(destination)
    if actual != config["ud"]["archive_sha256"]:
        raise RuntimeError(
            f"UD archive checksum mismatch: expected {config['ud']['archive_sha256']}, got {actual}"
        )
    root = treebank_root(data_root, config)
    if not root.exists():
        with tarfile.open(destination) as archive:
            archive.extractall(destination.parent, filter="data")


def prepare_stanza(data_root: Path, config: dict[str, Any], languages: set[str]) -> None:
    models_dir = data_root / "stanza"
    for language in config["languages"]:
        if languages and language["tag"] not in languages:
            continue
        print(f"Downloading Stanza model for {language['tag']}", flush=True)
        download_models(language["tag"], models_dir)


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _analyzer_artifacts(analyzer_name: str, language: str, data_root: Path) -> list[dict[str, Any]]:
    if analyzer_name == "unicode":
        return []
    if analyzer_name == "stanza":
        stanza_key = "zh-hans" if language == "zh" else language
        root = data_root / "stanza" / stanza_key
        paths = sorted(path for path in root.rglob("*") if path.is_file())
    else:
        import simplemma

        package = Path(simplemma.__file__).resolve().parent
        paths = [package / "strategies" / "dictionaries" / "data" / f"{language}.plzma"]
    return [
        {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in paths
    ]


def _query_keys(
    query: Query,
    analyzer: Any,
    lexicon: dict[str, set[tuple[str, str | None]]],
) -> tuple[list[str], list[str]]:
    exact = [" ".join(token.normalized for token in tokens_with_spans(query.surface))]
    lemma_candidates: set[str] = set()
    if not analyzer.morphology_available:
        return exact, []
    analyzed = analyzer.analyze(query.surface)
    if analyzed.tokens and all(token.lemma for token in analyzed.tokens):
        lemma_candidates.add(" ".join(token.lemma or "" for token in analyzed.tokens))
    if len(analyzed.tokens) == 1:
        form = production_key(analyzed.tokens[0].surface)
        lemma_candidates.update(
            production_key(lemma) or "" for lemma, _upos in lexicon.get(form or "", set())
        )
    lemma_candidates.discard("")
    return exact, sorted(lemma_candidates)


def _observed_lexicon(streams: list[Any]) -> dict[str, set[tuple[str, str | None]]]:
    lexicon: dict[str, set[tuple[str, str | None]]] = {}
    for token in streams:
        if token.route != "lemma" or not token.lemma:
            continue
        surface = production_key(token.surface)
        if surface:
            lexicon.setdefault(surface, set()).add((token.lemma, token.upos))
    return lexicon


def _retrieval_metrics(
    path: Path,
    layout: str,
    queries: list[Query],
    analyzer: Any,
    training_lexicon: dict[str, set[tuple[str, str | None]]],
    test_sentences: list[Any],
) -> list[dict[str, Any]]:
    gold_by_lemma: dict[str, set[str]] = {}
    for query in queries:
        intended = strict_key(query.intended_lemma)
        gold_by_lemma.setdefault(intended or "", set())
    for sentence in test_sentences:
        for word in sentence.words:
            gold_lemma = strict_key(word.lemma)
            if gold_lemma in gold_by_lemma:
                gold_by_lemma[gold_lemma].add(
                    occurrence_id(sentence.sentence_id, word.start, word.end)
                )
    rows = []
    for query in queries:
        exact_keys, lemma_keys = _query_keys(query, analyzer, training_lexicon)
        exact = set().union(*(search_database(path, layout, "exact", key) for key in exact_keys))
        lemma_matches = (
            set().union(*(search_database(path, layout, "lemma", key) for key in lemma_keys))
            if lemma_keys
            else set()
        )
        auto = auto_search(path, layout, exact_keys, lemma_keys)
        intended_ids = gold_by_lemma.get(strict_key(query.intended_lemma) or "", set())
        lemma_ids = {match.occurrence_id for match in lemma_matches}
        auto_ids = {match.occurrence_id for match in auto}
        rows.append(
            {
                "query_id": query.query_id,
                "surface": query.surface,
                "intended_lemma": query.intended_lemma,
                "selection_class": query.selection_class,
                "candidate_lemma_keys": lemma_keys,
                "exact_count": len(exact),
                "lemma_count": len(lemma_matches),
                "auto_count": len(auto),
                "deduplicated_expansion": len(auto_ids - {match.occurrence_id for match in exact}),
                "candidate_lemma_recall": round(
                    len(lemma_ids & intended_ids) / len(intended_ids), 6
                )
                if intended_ids
                else None,
                "intended_lemma_precision": round(len(lemma_ids & intended_ids) / len(lemma_ids), 6)
                if lemma_ids
                else None,
                "ambiguous_union_precision": (
                    round(len(auto_ids & intended_ids) / len(auto_ids), 6)
                    if auto_ids and query.selection_class == "ambiguous_form"
                    else None
                ),
            }
        )
    return rows


def worker(
    data_root: Path,
    config: dict[str, Any],
    language_tag: str,
    analyzer_name: str,
    output: Path,
) -> None:
    language = next(row for row in config["languages"] if row["tag"] == language_tag)
    paths = treebank_paths(data_root, config, language)
    training = read_conllu(paths["train"]) + read_conllu(paths["dev"])
    test_sentences = read_conllu(paths["test"])
    lexicon = build_training_lexicon(training)
    initialized = time.perf_counter()
    analyzer = get_analyzer(language_tag, analyzer_name, str(data_root / "stanza"))
    initialization_seconds = time.perf_counter() - initialized
    analysis_started = time.perf_counter()
    analyses = []
    unsupported_inputs = 0
    for sentence in test_sentences:
        try:
            analyses.append(analyzer.analyze(sentence.text))
        except InvalidAnalysisError:
            # Keep the row traceable without weakening the production offset contract.
            # The scorer treats the sentence as uncovered and the report records the
            # dedicated unsupported-input class without storing source text.
            analyses.append(Analysis((), analyzer.provenance))
            unsupported_inputs += 1
    analysis_seconds = time.perf_counter() - analysis_started
    analysis_peak_rss = _rss_bytes()
    quality = score_analysis(test_sentences, analyses, lexicon)
    if unsupported_inputs:
        quality["error_classes"]["unsupported_input"] = unsupported_inputs
    queries = select_queries(
        language_tag,
        test_sentences,
        lexicon,
        seed=config["seed"],
        max_inflected=config["query_selection"]["max_inflected_lemmas"],
        minimum_forms=config["query_selection"]["minimum_forms"],
        minimum_occurrences=config["query_selection"]["minimum_test_occurrences"],
        max_ambiguous=config["query_selection"]["max_ambiguous_forms"],
    )
    streams = build_streams(language_tag, test_sentences, analyses)
    observed_lexicon = _observed_lexicon(streams)
    database_dir = data_root / "indexes" / language_tag / analyzer_name
    database_dir.mkdir(parents=True, exist_ok=True)
    layouts = {}
    for layout in ("dual", "partial", "token"):
        layouts[layout] = build_database(database_dir / f"{layout}.sqlite3", layout, streams)
    layouts["partial"]["parity"] = verify_parity(
        Path(layouts["dual"]["path"]), Path(layouts["partial"]["path"]), "partial"
    )
    layouts["token"]["parity"] = verify_parity(
        Path(layouts["dual"]["path"]), Path(layouts["token"]["path"]), "token"
    )
    query_keys: list[tuple[str, str]] = []
    for query in queries:
        exact, lemmas = _query_keys(query, analyzer, observed_lexicon)
        query_keys.extend(("exact", key) for key in exact)
        query_keys.extend(("lemma", key) for key in lemmas)
    for layout, metrics in layouts.items():
        metrics["bytes_per_gold_token"] = round(
            metrics["size_bytes"] / max(quality["gold_words"], 1), 6
        )
        metrics["warm_query"] = warm_query_timings(
            Path(metrics["path"]),
            layout,
            query_keys,
            config["benchmark"]["warm_query_repetitions"],
        )
    diagnostics = []
    if language_tag == "en":
        for form in ("went", "gone"):
            diagnostic = analyzer.analyze(form)
            diagnostics.append(
                {
                    "form": form,
                    "expected_modern_lemma": "go",
                    "predicted_lemmas": [token.lemma for token in diagnostic.tokens],
                }
            )
    token_count = sum(len(sentence.words) for sentence in test_sentences)
    artifacts = _analyzer_artifacts(analyzer_name, language_tag, data_root)
    result = {
        "run_identity": run_identity(config),
        "language": language_tag,
        "treebank": language["treebank"],
        "analyzer": analyzer_name,
        "status": "complete",
        "reason": None,
        "provenance": analyzer.provenance.as_dict(),
        "quality": quality,
        "cost": {
            "cold_initialization_seconds": round(initialization_seconds, 6),
            "analysis_peak_rss_bytes": analysis_peak_rss,
            "worker_peak_rss_bytes": _rss_bytes(),
            "analyzer_disk_bytes": sum(item["size_bytes"] for item in artifacts),
            "analyzer_artifacts": artifacts,
            "analysis_seconds": round(analysis_seconds, 6),
            "sentences_per_second": round(len(test_sentences) / analysis_seconds, 6),
            "tokens_per_second": round(token_count / analysis_seconds, 6),
            "sentence_count": len(test_sentences),
            "gold_token_count": token_count,
        },
        "queries": dataclass_rows(queries),
        "retrieval": _retrieval_metrics(
            Path(layouts["dual"]["path"]),
            "dual",
            queries,
            analyzer,
            observed_lexicon,
            test_sentences,
        ),
        "diagnostics": diagnostics,
        "storage": list(layouts.values()),
        "input": input_manifest(data_root, config, language),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_report(results: dict[str, Any]) -> str:
    complete = [row for row in results["quality"] if row["status"] == "complete"]
    pending = [row for row in results["quality"] if row["status"] not in {"complete", "N/A"}]
    lines = [
        "# Ten-language morphology and compact-index experiment",
        "",
        f"**Status:** {'Complete' if not pending else 'Incomplete'}",
        "",
        "This experiment compares the production Unicode, simplemma 1.2.0, and Stanza 1.14.0 adapters on Universal Dependencies 2.18. It records raw analyzer behavior; it does not add word-specific lemma overrides or change production selection or storage.",
        "",
        "## Coverage",
        "",
        "| Language | Unicode | simplemma | Stanza |",
        "| --- | --- | --- | --- |",
    ]
    for language in results["configuration"]["languages"]:
        values = []
        for analyzer in ("unicode", "simplemma", "stanza"):
            row = next(
                item
                for item in results["quality"]
                if item["language"] == language["tag"] and item["analyzer"] == analyzer
            )
            values.append(row["status"])
        lines.append(f"| {language['tag']} | {' | '.join(values)} |")
    lines.extend(
        [
            "",
            "Japanese and Chinese need model-based segmentation, and Japanese, Korean, and Chinese need Stanza for morphology. Unicode remains the dependency-free exact-search baseline. simplemma is evaluated only for its seven configured languages; unsupported cells are explicit `N/A` rows.",
            "",
            "## Quality",
            "",
            "| Language | Analyzer | Boundary F1 | Lemma coverage | Strict lemma | Folded key | Unseen | Ambiguous | MWT |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in complete:
        quality = row["quality"]
        lemma = quality["lemma"]
        lines.append(
            f"| {row['language']} | {row['analyzer']} | {quality['token_boundary']['f1']} | {lemma['coverage']} | {lemma['strict_accuracy']} | {lemma['production_key_accuracy']} | {lemma['unseen_accuracy']} | {lemma['ambiguous_accuracy']} | {quality['mwt']['accuracy']} |"
        )
    lines.extend(
        [
            "",
            "Strict lemma scoring uses Unicode NFC plus case folding and keeps accents. Folded-key accuracy separately reports equivalence under the production search normalizer. Error examples contain only token form, lemma, and POS fields; source sentences are not stored.",
            "",
            "## Runtime",
            "",
            "| Language | Analyzer | Init s | Tokens/s | Analysis RSS MB | Worker RSS MB | Analyzer MB |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in complete:
        cost = row["cost"]
        lines.append(
            f"| {row['language']} | {row['analyzer']} | {cost['cold_initialization_seconds']} | {cost['tokens_per_second']} | {round(cost['analysis_peak_rss_bytes'] / 1048576, 1)} | {round(cost['worker_peak_rss_bytes'] / 1048576, 1)} | {round(cost['analyzer_disk_bytes'] / 1048576, 1)} |"
        )
    lines.extend(
        [
            "",
            "## Storage",
            "",
            "| Language | Analyzer | Layout | MB | Bytes/gold token | Median ms | p95 ms | Parity |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in complete:
        for layout in row["storage"]:
            parity = layout.get("parity", {"passed": True})["passed"]
            timing = layout["warm_query"]
            lines.append(
                f"| {row['language']} | {row['analyzer']} | {layout['layout']} | {round(layout['size_bytes'] / 1048576, 2)} | {layout['bytes_per_gold_token']} | {timing['median_ms']} | {timing['p95_ms']} | {parity} |"
            )
    lines.extend(
        [
            "",
            "Physical occurrences, the form lexicon, token rows, and secondary indexes are separated by SQLite `dbstat` in each result. Because exact and lemma keys share pages in the production-shaped key table, `logical_breakdown` separately records their row counts and payload bytes. Compact timing is reported only after occurrence IDs, match routes, counts, and character spans match the dual-key reference.",
            "",
            "## Retrieval and anomalous mappings",
            "",
            "The query manifest is selected by stable SHA-256 order with seed `20260905`. It includes up to 20 lemmas with at least two observed test forms and five test occurrences, plus up to 10 forms that have multiple train/dev analyses. Results include exact count, deduplicated auto expansion, candidate-lemma recall, intended-lemma precision, and ambiguous-union precision.",
            "",
            "| Language | Analyzer | Queries | Exact | Auto | Expansion | Mean lemma recall | Mean lemma precision |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in complete:
        retrieval = row["retrieval"]
        recalls = [
            item["candidate_lemma_recall"]
            for item in retrieval
            if item["candidate_lemma_recall"] is not None
        ]
        precisions = [
            item["intended_lemma_precision"]
            for item in retrieval
            if item["intended_lemma_precision"] is not None
        ]
        lines.append(
            f"| {row['language']} | {row['analyzer']} | {len(retrieval)} | {sum(item['exact_count'] for item in retrieval)} | {sum(item['auto_count'] for item in retrieval)} | {sum(item['deduplicated_expansion'] for item in retrieval)} | {round(statistics.mean(recalls), 6) if recalls else None} | {round(statistics.mean(precisions), 6) if precisions else None} |"
        )
    lines.extend(
        [
            "",
            "simplemma's raw English dictionary maps `went` to `wend`. That mapping is historically explainable but incorrect for ordinary modern-English retrieval, where `went` is the past tense of `go`. The experiment records it as a false positive. The production system intentionally preserves available observed candidates and ranks exact matches first; later ranking and the dictionary-article LLM can reject semantically unsuitable clips.",
            "",
            "## Reproduction",
            "",
            "```console",
            "uv sync --extra dev --extra nlp",
            "uv run python experiments/morphological-retrieval-multilingual/run.py preflight",
            "uv run python experiments/morphological-retrieval-multilingual/run.py prepare",
            "uv run python experiments/morphological-retrieval-multilingual/run.py run",
            "```",
            "",
            "UD files and Stanza weights remain under `data/experiments/morphological-retrieval-multilingual/`. Preparation is the only command that accesses the network. Preflight reports every missing split, package, license mismatch, and model; the run never silently narrows the matrix.",
            "",
            "## Decision",
            "",
            "| Language | Recommended morphology analyzer | Effective lemma score |",
            "| --- | --- | ---: |",
        ]
    )
    for language in results["configuration"]["languages"]:
        candidates = []
        for row in complete:
            if row["language"] != language["tag"] or row["analyzer"] == "unicode":
                continue
            lemma = row["quality"]["lemma"]
            if lemma["coverage"] is not None and lemma["strict_accuracy"] is not None:
                candidates.append((lemma["coverage"] * lemma["strict_accuracy"], row["analyzer"]))
        if candidates:
            score, analyzer = max(candidates)
            lines.append(f"| {language['tag']} | {analyzer} | {round(score, 6)} |")
        else:
            lines.append(f"| {language['tag']} | Pending | N/A |")
    sizes: dict[str, int] = {}
    medians: dict[str, list[float]] = {}
    parity_passed = True
    for row in complete:
        for layout in row["storage"]:
            name = layout["layout"]
            sizes[name] = sizes.get(name, 0) + layout["size_bytes"]
            median = layout["warm_query"]["median_ms"]
            if median is not None:
                medians.setdefault(name, []).append(median)
            parity_passed = parity_passed and layout.get("parity", {"passed": True})["passed"]
    if sizes.get("dual") and sizes.get("token"):
        saving = 100 * (1 - sizes["token"] / sizes["dual"])
        dual_median = statistics.median(medians.get("dual", [0.0]))
        token_median = statistics.median(medians.get("token", [0.0]))
        latency_ratio = token_median / dual_median if dual_median else float("inf")
        if parity_passed and saving >= 25 and latency_ratio <= 5:
            index_decision = (
                f"The token-position prototype preserves semantics and saves {saving:.1f}% "
                f"across completed rows at {latency_ratio:.2f}× median query time. This is material "
                "enough for a separate production migration plan."
            )
        else:
            index_decision = (
                f"The token-position prototype saves {saving:.1f}% at {latency_ratio:.2f}× median "
                "query time. Keep the dual-key layout until parity and the cost threshold justify "
                "a separate migration."
            )
    else:
        index_decision = "The index recommendation is pending complete storage rows."
    lines.extend(
        [
            "",
            "The analyzer recommendation maximizes strict lemma accuracy multiplied by end-to-end lemma coverage. Runtime and downstream LLM filtering remain deployment considerations; this experiment does not change production defaults.",
            "",
            index_decision,
            "",
            "Wiktionary-derived candidates remain deferred. Any analyzer or schema change will use a separate implementation plan.",
            "",
            "## Limitations",
            "",
            "UD written-language treebanks are comparable gold data rather than a direct sample of YouTube speech. A UD lemma match can still be unhelpful for a learner, and a linguistically valid ambiguity can lower intended-sense precision. Timings describe the recorded machine and cold-process protocol; they are not service capacity estimates.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_all(data_root: Path, config: dict[str, Any], languages: set[str]) -> dict[str, Any]:
    document = result_document(config)
    document["configuration"] = config
    document["environment"] = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    selected = {
        row["tag"] for row in config["languages"] if not languages or row["tag"] in languages
    }
    work = data_root / "results"
    expected_identity = run_identity(config)
    for row in document["quality"]:
        if row["language"] not in selected or row["status"] == "N/A":
            continue
        output = work / f"{row['language']}-{row['analyzer']}.json"
        reusable = False
        if output.exists():
            try:
                reusable = json.loads(output.read_text()).get("run_identity") == expected_identity
            except (json.JSONDecodeError, OSError):
                reusable = False
        if reusable:
            print(f"Reusing {row['language']} / {row['analyzer']}", flush=True)
        else:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker",
                "--data-root",
                str(data_root),
                "--language",
                row["language"],
                "--analyzer",
                row["analyzer"],
                "--output",
                str(output),
            ]
            print(f"Running {row['language']} / {row['analyzer']}", flush=True)
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            if completed.returncode:
                row["status"] = "error"
                row["reason"] = (completed.stderr or completed.stdout)[-4000:]
                continue
        result = json.loads(output.read_text())
        row.update(result)
        existing_query_ids = {item["query_id"] for item in document["queries"]}
        document["queries"].extend(
            item for item in result["queries"] if item["query_id"] not in existing_query_ids
        )
        document["retrieval"].extend(
            {"language": row["language"], "analyzer": row["analyzer"], **item}
            for item in result["retrieval"]
        )
        document["storage"].extend(
            {"language": row["language"], "analyzer": row["analyzer"], **item}
            for item in result["storage"]
        )
        document["diagnostics"].extend(
            {"language": row["language"], "analyzer": row["analyzer"], **item}
            for item in result["diagnostics"]
        )
        document["inputs"][row["language"]] = result["input"]
    document["complete"] = all(row["status"] in {"complete", "N/A"} for row in document["quality"])
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "prepare", "run", "worker"))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--languages", nargs="*", default=[])
    parser.add_argument("--skip-ud", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--language")
    parser.add_argument("--analyzer", choices=("unicode", "simplemma", "stanza"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    config = load_config()
    data_root = args.data_root.resolve()
    selected = set(args.languages)
    if args.command == "preflight":
        status = preflight(data_root, config)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        raise SystemExit(0 if status["ready"] else 1)
    if args.command == "prepare":
        if not args.skip_ud:
            prepare_ud(data_root, config)
        if not args.skip_models:
            prepare_stanza(data_root, config, selected)
        print(json.dumps(preflight(data_root, config), ensure_ascii=False, indent=2))
        return
    if args.command == "worker":
        if not args.language or not args.analyzer or not args.output:
            parser.error("worker requires --language, --analyzer, and --output")
        worker(data_root, config, args.language, args.analyzer, args.output)
        return
    status = preflight(data_root, config)
    required_languages = [
        row for row in status["languages"] if not selected or row["language"] in selected
    ]
    if any(row["missing_splits"] for row in required_languages):
        raise SystemExit("UD inputs are missing; run the explicit prepare command")
    results = run_all(data_root, config, selected)
    args.results.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    args.report.write_text(render_report(results))
    if not results["complete"]:
        raise SystemExit("Experiment finished with incomplete rows; inspect results.json")


if __name__ == "__main__":
    main()
