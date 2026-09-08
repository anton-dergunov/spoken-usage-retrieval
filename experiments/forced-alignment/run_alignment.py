#!/usr/bin/env python
"""Staged runner for the forced-alignment experiment.

    preflight   inventory, audio readiness, and a measured throughput budget
    reference   fetch the automatic caption track for authored videos
    sample      freeze the sampled segments
    align       run every system over every sampled segment
    score       compare each system against the reference and aggregate
    review-export / review-html / review-import   the blind karaoke pass
    report      write results.json

Every stage checkpoints after each item, so an interrupted run resumes rather than restarts.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alignment_eval import (  # noqa: E402
    BASELINES,
    CUE_INTERPOLATED,
    CUE_START,
    RESULT_SCHEMA_VERSION,
    ExperimentConfig,
    ResultRow,
    ReviewItem,
    SystemResult,
    choose_review_clips,
    cue_interpolated_words,
    cue_start_words,
    engine_agreement,
    match_words,
    reference_starts,
    review_agreement,
    stable_order,
    summarize,
)

from speech_retrieval.alignment import resolve_model  # noqa: E402
from speech_retrieval.audio import audio_availability, prepare_clip  # noqa: E402
from speech_retrieval.captions import automatic_units  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config-v1.json"
DEFAULT_DATA_DIR = REPO / "data"
DEFAULT_RUN_ROOT = REPO / "data" / "experiments" / "forced-alignment"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "results.json"


def now() -> str:
    return datetime.now(UTC).isoformat()


def load_config(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate_json(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_rows(path: Path, rows: Sequence[ResultRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(row.model_dump_json() + "\n")


def read_rows(path: Path) -> list[ResultRow]:
    if not path.is_file():
        return []
    rows: list[ResultRow] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(ResultRow.model_validate_json(line))
    return rows


def run_root(args: argparse.Namespace) -> Path:
    return Path(args.run_root) / args.run_id


# --- Inventory ------------------------------------------------------------------------------


def track_metadata(data_dir: Path, language: str) -> dict[str, list[dict[str, Any]]]:
    """Every cached caption track per video, including ones the index did not choose."""
    root = data_dir / "raw" / "corpora" / language
    tracks: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.glob("*/*/metadata.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_track_dir"] = str(path.parent)
        tracks.setdefault(path.parents[1].name, []).append(payload)
    return tracks


def load_segments(data_dir: Path, language: str) -> list[dict[str, Any]]:
    path = data_dir / "derived" / "corpora" / language / "segments.jsonl"
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def reference_path(run_root_dir: Path, video_key: str) -> Path:
    return run_root_dir / "reference-captions" / f"{video_key}.json3"


def load_reference_units(
    data_dir: Path, run_root_dir: Path, language: str, video_key: str, tracks: list[dict[str, Any]]
) -> list[Any]:
    """The automatic track's word timings for one video.

    Prefers the ``*-orig`` automatic track: YouTube also exposes machine-*translated*
    automatic tracks under the plain language code, and those carry timings for a translation
    rather than for the speech.
    """
    for payload in tracks:
        if payload.get("caption_kind") != "automatic":
            continue
        raw = Path(payload["_track_dir"]) / "subtitles.raw.json3"
        if raw.is_file():
            return automatic_units(json.loads(raw.read_text(encoding="utf-8")))
    fetched = reference_path(run_root_dir, video_key)
    if fetched.is_file():
        return automatic_units(json.loads(fetched.read_text(encoding="utf-8")))
    return []


# --- preflight -------------------------------------------------------------------------------


def command_preflight(args: argparse.Namespace, config: ExperimentConfig) -> int:
    data_dir = Path(args.data_dir)
    report: dict[str, Any] = {"generated_at": now(), "languages": {}}
    for language in config.languages:
        segments = load_segments(data_dir, language)
        tracks = track_metadata(data_dir, language)
        if not segments:
            report["languages"][language] = {"indexed_segments": 0, "note": "no indexed corpus"}
            continue
        by_video: dict[str, dict[str, Any]] = {}
        for row in segments:
            video = by_video.setdefault(
                row["video_key"], {"segments": 0, "kind": None, "audio": None}
            )
            video["segments"] += 1
        for video_key, entry in by_video.items():
            kinds = {item.get("caption_kind") for item in tracks.get(video_key, [])}
            entry["kind"] = "authored" if "manual" in kinds else "automatic"
            entry["has_reference"] = (
                "automatic" in kinds or reference_path(run_root(args), video_key).is_file()
            )
            availability = audio_availability(data_dir, language=language, video_key=video_key)
            entry["audio"] = availability.status
        authored = [item for item in by_video.values() if item["kind"] == "authored"]
        automatic = [item for item in by_video.values() if item["kind"] == "automatic"]
        report["languages"][language] = {
            "indexed_segments": len(segments),
            "videos": len(by_video),
            "authored_videos": len(authored),
            "automatic_videos": len(automatic),
            "authored_with_reference": sum(1 for item in authored if item["has_reference"]),
            "audio_ready": sum(1 for item in by_video.values() if item["audio"] == "ready"),
            "audio_missing": sum(1 for item in by_video.values() if item["audio"] != "ready"),
            "authored_segments": sum(item["segments"] for item in authored),
            "automatic_segments": sum(item["segments"] for item in automatic),
        }
    report["budget"] = _throughput_budget(args, config)
    write_json(run_root(args) / "preflight.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _throughput_budget(args: argparse.Namespace, config: ExperimentConfig) -> dict[str, Any]:
    """Report the largest per-cell sample that fits the configured time budget.

    Measured rather than assumed: throughput depends on the machine, the device, and which
    checkpoints are configured, so a fixed sample size would silently overrun elsewhere.
    """
    measured = args.seconds_per_clip
    cells = max(len(config.languages) * 2, 1)
    systems = len(config.models)
    per_segment = measured * systems if measured else None
    if not per_segment:
        return {"note": "pass --seconds-per-clip from an align run to size the sample"}
    affordable = int(config.sampling.time_budget_seconds / per_segment / cells)
    return {
        "seconds_per_clip_per_model": measured,
        "models": systems,
        "cells": cells,
        "time_budget_seconds": config.sampling.time_budget_seconds,
        "affordable_per_cell": affordable,
        "configured_per_cell": config.sampling.target_per_cell,
        "recommended_per_cell": min(affordable, config.sampling.target_per_cell),
    }


# --- reference ---------------------------------------------------------------------------------


def command_reference(args: argparse.Namespace, config: ExperimentConfig) -> int:
    """Fetch the automatic caption track for videos whose canonical track is authored.

    The main pipeline deliberately stops looking once it finds an authored track, so these
    are not in the cache. They are written under the run directory rather than into the
    corpus cache, which keeps the corpus's provenance model intact: this is experiment
    evidence, not indexed material.
    """
    import yt_dlp

    data_dir = Path(args.data_dir)
    root = run_root(args)
    fetched = skipped = failed = 0
    for language in config.languages:
        tracks = track_metadata(data_dir, language)
        segments = load_segments(data_dir, language)
        video_ids = {row["video_key"]: row["video_id"] for row in segments}
        for video_key, entries in sorted(tracks.items()):
            kinds = {item.get("caption_kind") for item in entries}
            if "automatic" in kinds or "manual" not in kinds:
                skipped += 1
                continue
            target = reference_path(root, video_key)
            if target.is_file():
                skipped += 1
                continue
            video_id = video_ids.get(video_key)
            if not video_id:
                failed += 1
                continue
            try:
                payload = _fetch_automatic_track(yt_dlp, video_id, language)
            except Exception as error:  # noqa: BLE001 - a probe failure is data, not a crash
                print(f"  {video_key}: {error}", file=sys.stderr)
                failed += 1
                continue
            if payload is None:
                failed += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            fetched += 1
            print(f"  fetched reference for {video_key}")
    summary = {"fetched": fetched, "skipped": skipped, "failed": failed}
    print(json.dumps(summary, indent=2))
    return 0


def _fetch_automatic_track(yt_dlp: Any, video_id: str, language: str) -> dict[str, Any] | None:
    """Download the original-language ASR caption track as json3.

    Prefers ``<language>-orig``. YouTube also exposes machine-translated automatic tracks
    under the bare language code; those describe a translation, not the speech, and would be
    a meaningless timing reference.
    """
    import urllib.request

    options = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )
    automatic = info.get("automatic_captions") or {}
    for key in (f"{language}-orig", language):
        entries = automatic.get(key)
        if not entries:
            continue
        for entry in entries:
            if entry.get("ext") == "json3" and entry.get("url"):
                with urllib.request.urlopen(entry["url"], timeout=60) as response:
                    return json.loads(response.read().decode("utf-8"))
    return None


# --- sample -------------------------------------------------------------------------------------


def command_sample(args: argparse.Namespace, config: ExperimentConfig) -> int:
    data_dir = Path(args.data_dir)
    root = run_root(args)
    sampling = config.sampling
    target = args.per_cell or sampling.target_per_cell
    rows: list[ResultRow] = []
    for language in config.languages:
        tracks = track_metadata(data_dir, language)
        segments = load_segments(data_dir, language)
        if not segments:
            continue
        kinds = {
            video_key: (
                "authored"
                if any(item.get("caption_kind") == "manual" for item in entries)
                else "automatic"
            )
            for video_key, entries in tracks.items()
        }
        eligible: dict[str, list[dict[str, Any]]] = {"authored": [], "automatic": []}
        for row in segments:
            source_class = kinds.get(row["video_key"], "automatic")
            duration = float(row["clip_end"]) - float(row["clip_start"])
            if int(row.get("token_count") or 0) < sampling.min_tokens:
                continue
            if not 0.5 < duration <= sampling.max_clip_seconds:
                continue
            availability = audio_availability(
                data_dir, language=language, video_key=row["video_key"]
            )
            if availability.status != "ready":
                continue
            eligible[source_class].append(row)
        for source_class, candidates in eligible.items():
            ordered = stable_order(candidates, "id", sampling.seed)
            # Spread the sample across videos rather than taking a run from one speaker.
            by_video: dict[str, list[dict[str, Any]]] = {}
            for row in ordered:
                by_video.setdefault(row["video_key"], []).append(row)
            picked: list[dict[str, Any]] = []
            while len(picked) < target and any(by_video.values()):
                for video_key in sorted(by_video):
                    if len(picked) >= target:
                        break
                    if by_video[video_key]:
                        picked.append(by_video[video_key].pop(0))
            for row in picked:
                channel = next(
                    (
                        item.get("channel_config_id")
                        for item in tracks.get(row["video_key"], [])
                        if item.get("channel_config_id")
                    ),
                    None,
                )
                rows.append(
                    ResultRow(
                        run_id=args.run_id,
                        segment_id=row["id"],
                        video_key=row["video_key"],
                        channel=channel,
                        language=language,
                        source_class=source_class,  # type: ignore[arg-type]
                        text=row["text"],
                        clip_start=float(row["clip_start"]),
                        clip_end=float(row["clip_end"]),
                        stage="sampled",
                    )
                )
    write_rows(root / "sampled.jsonl", rows)
    counts: dict[str, int] = {}
    for sampled in rows:
        cell = f"{sampled.language}/{sampled.source_class}"
        counts[cell] = counts.get(cell, 0) + 1
    print(json.dumps({"run_id": args.run_id, "total": len(rows), "cells": counts}, indent=2))
    return 0


# --- align -----------------------------------------------------------------------------------------


def command_align(args: argparse.Namespace, config: ExperimentConfig) -> int:
    from speech_retrieval.alignment_ctc import build_aligner

    data_dir = Path(args.data_dir)
    root = run_root(args)
    rows = read_rows(root / "sampled.jsonl")
    if not rows:
        print("no sampled rows; run sample first", file=sys.stderr)
        return 1
    if args.limit:
        rows = rows[: args.limit]

    aligners: dict[str, Any] = {}
    durations: list[float] = []
    output = root / "aligned.jsonl"
    started = time.time()
    for index, row in enumerate(rows):
        try:
            clip = prepare_clip(
                data_dir,
                language=row.language,
                video_key=row.video_key,
                start=row.clip_start,
                end=row.clip_end,
                padding=0.0,
            )
        except Exception as error:  # noqa: BLE001 - a clip failure is a recorded row, not a crash
            row.stage = "clip_failed"
            row.error = str(error)
            write_rows(output, rows)
            continue
        row.clip = str(clip.path)
        row.stage = "aligned"
        row.systems = []

        for spec in config.models:
            model = resolve_model(row.language, profile=spec.profile)
            if model is None:
                row.systems.append(
                    SystemResult(
                        system=spec.name,
                        status="unavailable",
                        reason=f"no {spec.profile} model for {row.language}",
                    )
                )
                continue
            key = f"{spec.profile}:{model.model_id}"
            if key not in aligners:
                aligners[key] = build_aligner(
                    model, device=args.device or None, num_threads=args.threads or None
                )
            begin = time.time()
            result = aligners[key].align(row.text, row.language, clip)
            elapsed = time.time() - begin
            durations.append(elapsed / max(clip.duration, 0.001))
            confidences = [
                group.confidence for group in result.groups if group.confidence is not None
            ]
            row.systems.append(
                SystemResult(
                    system=spec.name,
                    status=result.status,
                    coverage=result.coverage,
                    mean_confidence=(
                        round(statistics.fmean(confidences), 4) if confidences else None
                    ),
                    seconds=round(elapsed, 4),
                    reason=result.reason,
                )
            )
            # Timed words are attached at score time; store them on the row for reuse.
            _stash_words(row, spec.name, result)
        write_rows(output, rows)
        if (index + 1) % 10 == 0:
            print(f"  {index + 1}/{len(rows)} aligned ({time.time() - started:.0f}s)")

    summary = {
        "rows": len(rows),
        "wall_seconds": round(time.time() - started, 1),
        "median_seconds_per_audio_second": (
            round(statistics.median(durations), 4) if durations else None
        ),
        "device": args.device or "auto",
    }
    write_json(root / "align-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


_WORDS: dict[tuple[str, str], list[tuple[str, float]]] = {}


def _stash_words(row: ResultRow, system: str, result: Any) -> None:
    """Keep timed words on the row as comparisons against a null reference for now.

    ``score`` replaces the reference side once the automatic track is loaded; storing them
    here avoids re-running the models when only the scoring changes.
    """
    words = [
        (group.text, group.start)
        for group in result.groups
        if group.match_status == "matched" and group.start is not None
    ]
    entry = row.system(system)
    if entry is None:
        return
    from alignment_eval import WordComparison

    entry.comparisons = [
        WordComparison(text=text, aligned_start=start, reference_start=start)
        for text, start in words
    ]


# --- score ------------------------------------------------------------------------------------------


def command_score(args: argparse.Namespace, config: ExperimentConfig) -> int:
    data_dir = Path(args.data_dir)
    root = run_root(args)
    rows = read_rows(root / "aligned.jsonl")
    if not rows:
        print("no aligned rows; run align first", file=sys.stderr)
        return 1

    reference_cache: dict[tuple[str, str], list[Any]] = {}
    tracks_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        if row.language not in tracks_cache:
            tracks_cache[row.language] = track_metadata(data_dir, row.language)
        key = (row.language, row.video_key)
        if key not in reference_cache:
            reference_cache[key] = load_reference_units(
                data_dir,
                root,
                row.language,
                row.video_key,
                tracks_cache[row.language].get(row.video_key, []),
            )
        units = [
            unit for unit in reference_cache[key] if row.clip_start <= unit.start <= row.clip_end
        ]
        reference = reference_starts(units, row.clip_start)
        row.reference_words = len(reference)

        # Baselines are computed here rather than at align time: they need no model, and
        # keeping them in one place makes the comparison obviously like-for-like.
        duration = row.clip_end - row.clip_start
        baselines = {
            CUE_START: cue_start_words(row.text, 0.0),
            CUE_INTERPOLATED: cue_interpolated_words(row.text, 0.0, duration),
        }
        row.systems = [item for item in row.systems if item.system not in BASELINES]
        for name, words in baselines.items():
            row.systems.append(
                SystemResult(
                    system=name,
                    status="complete",
                    coverage=1.0,
                    comparisons=match_words(words, reference) if reference else [],
                )
            )
        for entry in row.systems:
            if entry.system in BASELINES:
                continue
            aligned = [(item.text, item.aligned_start) for item in entry.comparisons]
            entry.comparisons = match_words(aligned, reference) if reference else []
        row.stage = "scored"
    write_rows(root / "scored.jsonl", rows)

    systems = [spec.name for spec in config.models] + list(BASELINES)
    summaries = []
    for language in sorted({row.language for row in rows}):
        for source_class in ("authored", "automatic"):
            cell = [
                row for row in rows if row.language == language and row.source_class == source_class
            ]
            for system in systems:
                summary = summarize(
                    cell,
                    system,
                    tolerances=config.tolerances,
                    minimum_per_cell=config.sampling.minimum_per_cell,
                )
                if summary:
                    summaries.append(summary.model_dump())
    payload = {
        "generated_at": now(),
        "summaries": summaries,
        "engine_agreement": (
            engine_agreement(rows, config.models[0].name, config.models[1].name)
            if len(config.models) > 1
            else {}
        ),
        "reference_coverage": {
            "rows": len(rows),
            "rows_with_reference": sum(1 for row in rows if row.reference_words),
        },
    }
    write_json(root / "scores.json", payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


# --- review ---------------------------------------------------------------------------------------------


def command_review_export(args: argparse.Namespace, config: ExperimentConfig) -> int:
    root = run_root(args)
    rows = read_rows(root / "scored.jsonl") or read_rows(root / "aligned.jsonl")
    if not rows:
        print("no rows to review", file=sys.stderr)
        return 1
    systems = [spec.name for spec in config.models] + [CUE_INTERPOLATED]
    items = choose_review_clips(rows, systems, config.sampling)
    worksheet = {
        "run_id": args.run_id,
        "rubric_version": "alignment-review-v1",
        "generated_at": now(),
        "systems": systems,
        "total_rows": len(rows),
        "items": [item.model_dump() for item in items],
    }
    write_json(root / "review-worksheet.json", worksheet)
    print(json.dumps({"items": len(items), "systems": systems}, indent=2))
    return 0


def command_review_html(args: argparse.Namespace, _config: ExperimentConfig) -> int:
    from alignment_review_app import render_review_app

    root = run_root(args)
    worksheet_path = root / "review-worksheet.json"
    if not worksheet_path.is_file():
        print("run review-export first", file=sys.stderr)
        return 1
    worksheet = json.loads(worksheet_path.read_text(encoding="utf-8"))
    rows = {row.segment_id: row for row in read_rows(root / "scored.jsonl")}
    page = root / "review.html"
    html = render_review_app(worksheet, rows, embed_audio=not args.no_audio)
    page.write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "page": str(page),
                "bytes": page.stat().st_size,
                "items": len(worksheet["items"]),
            },
            indent=2,
        )
    )
    return 0


def command_review_import(args: argparse.Namespace, _config: ExperimentConfig) -> int:
    root = run_root(args)
    filled = json.loads(Path(args.worksheet).read_text(encoding="utf-8"))
    items = [ReviewItem.model_validate(item) for item in filled["items"]]
    judged = [item for item in items if item.ratings]
    write_json(
        root / "reviewed.json",
        {"run_id": args.run_id, "imported_at": now(), "items": [i.model_dump() for i in items]},
    )
    rows = read_rows(root / "scored.jsonl")
    agreement = review_agreement(items, rows)
    write_json(root / "review-agreement.json", agreement)
    print(json.dumps({"judged": len(judged), "agreement": agreement}, indent=2))
    return 0


# --- report -----------------------------------------------------------------------------------------------


def command_report(args: argparse.Namespace, config: ExperimentConfig) -> int:
    root = run_root(args)
    scores_path = root / "scores.json"
    if not scores_path.is_file():
        print("run score first", file=sys.stderr)
        return 1
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    agreement_path = root / "review-agreement.json"
    payload = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "run_id": args.run_id,
        "generated_at": now(),
        "config": config.model_dump(),
        "align_summary": _maybe_json(root / "align-summary.json"),
        "preflight": _maybe_json(root / "preflight.json"),
        "scores": scores,
        "review_agreement": _maybe_json(agreement_path),
    }
    write_json(Path(args.results), payload)
    print(_render_table(scores))
    return 0


def _maybe_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _render_table(scores: dict[str, Any]) -> str:
    header = (
        f"{'cell':22} {'system':22} {'n':>4} {'vid':>4} {'words':>6} "
        f"{'med|Δ|':>8} {'p90':>7} {'signed':>8} {'<200ms':>7}"
    )
    lines = [header, "-" * len(header)]
    for row in scores.get("summaries", []):
        cell = f"{row['language']}/{row['source_class']}"
        flag = " *" if row.get("indicative_only") else ""
        lines.append(
            f"{cell:22} {row['system']:22} {row['segments']:4d} {row['videos']:4d} "
            f"{row['words']:6d} {row['median_abs']:8.3f} {row['p90_abs']:7.3f} "
            f"{row['median_signed']:8.3f} {row['within'].get('200ms', 0):7.3f}{flag}"
        )
    engine = scores.get("engine_agreement") or {}
    if engine.get("pairs"):
        lines.append("")
        lines.append(
            f"engine-engine agreement: median |Δ| {engine['median_abs']:.3f}s, "
            f"within 200ms {engine['within_200ms']:.3f}, over {engine['pairs']} word pairs"
        )
    lines.append("")
    lines.append("* cell below the minimum sample size; indicative only")
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default="run-1")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser("preflight", help="Inventory and budget.")
    preflight.add_argument(
        "--seconds-per-clip",
        type=float,
        default=0.0,
        help="Measured seconds per clip per model, to size the sample.",
    )
    preflight.set_defaults(handler=command_preflight)

    reference = commands.add_parser(
        "reference", help="Fetch automatic caption tracks for authored videos."
    )
    reference.set_defaults(handler=command_reference)

    sample = commands.add_parser("sample", help="Freeze the sample.")
    sample.add_argument("--per-cell", type=int, default=0)
    sample.set_defaults(handler=command_sample)

    align = commands.add_parser("align", help="Run every configured system.")
    align.add_argument("--limit", type=int, default=0)
    align.add_argument("--device", default="")
    align.add_argument("--threads", type=int, default=0)
    align.set_defaults(handler=command_align)

    score = commands.add_parser("score", help="Compare against the reference and aggregate.")
    score.set_defaults(handler=command_score)

    export = commands.add_parser("review-export", help="Freeze the blind review subset.")
    export.set_defaults(handler=command_review_export)

    html = commands.add_parser("review-html", help="Render the karaoke review page.")
    html.add_argument("--no-audio", action="store_true")
    html.set_defaults(handler=command_review_html)

    review_import = commands.add_parser("review-import", help="Import a filled worksheet.")
    review_import.add_argument("--worksheet", required=True)
    review_import.set_defaults(handler=command_review_import)

    report = commands.add_parser("report", help="Write results.json and print the table.")
    report.set_defaults(handler=command_report)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(Path(args.config))
    handler = args.handler
    return int(handler(args, config))


if __name__ == "__main__":
    raise SystemExit(main())
