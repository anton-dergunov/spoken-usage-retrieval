#!/usr/bin/env python3
"""Exercise the production translation prompt against representative indexed clips.

This is deliberately excluded from CI because it spends provider calls. Results go under ignored
``data/experiments`` and contain no API key.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import median
from typing import Any

from dotenv import load_dotenv

from speech_retrieval.translations import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    TRANSLATION_SCHEMA_VERSION,
    GeminiTranslationProvider,
    ProviderTranslationRequest,
    validate_provider_output,
)


def samples(
    database: Path, count: int, segment_ids: list[str] | None = None
) -> list[dict[str, str]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        if segment_ids:
            placeholders = ",".join("?" for _ in segment_ids)
            fetched = connection.execute(
                f"""SELECT s.segment_id, s.source_language, s.text, s.segments_json,
                           s.boundary_reason,
                           t.caption_kind
                    FROM segments s JOIN transcripts t ON t.track_id = s.track_id
                    WHERE s.segment_id IN ({placeholders})""",
                segment_ids,
            ).fetchall()
            by_id = {row["segment_id"]: row for row in fetched}
            missing = [segment_id for segment_id in segment_ids if segment_id not in by_id]
            if missing:
                raise ValueError(f"unknown segment IDs: {', '.join(missing)}")
            return [dict(by_id[segment_id]) for segment_id in segment_ids]
        rows = connection.execute(
            """SELECT s.segment_id, s.source_language, s.text, s.segments_json,
                      s.boundary_reason,
                      t.caption_kind
               FROM segments s JOIN transcripts t ON t.track_id = s.track_id
               WHERE length(s.text) BETWEEN 12 AND 260
               ORDER BY t.caption_kind, s.boundary_reason, s.segment_id"""
        ).fetchall()
    if not rows:
        raise ValueError(f"no suitable segments in {database}")
    count = min(count, len(rows))
    indexes = (
        [0]
        if count == 1
        else sorted({round(index * (len(rows) - 1) / (count - 1)) for index in range(count)})
    )
    return [dict(rows[index]) for index in indexes]


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in results if item["status"] == "valid"]
    latencies = sorted(float(item["latency_ms"]) for item in valid)
    quality = [item["alignment_quality"] for item in valid if item.get("alignment_quality")]
    targets = sorted({item["target_language"] for item in results})
    return {
        "calls": len(results),
        "valid": len(valid),
        "failed": len(results) - len(valid),
        "valid_fraction": round(len(valid) / max(1, len(results)), 4),
        "by_target": {
            target: {
                "calls": sum(item["target_language"] == target for item in results),
                "valid": sum(
                    item["target_language"] == target and item["status"] == "valid"
                    for item in results
                ),
            }
            for target in targets
        },
        "latency_ms": {
            "median": round(median(latencies), 2) if latencies else None,
            "p95": latencies[min(len(latencies) - 1, ceil(len(latencies) * 0.95) - 1)]
            if latencies
            else None,
        },
        "alignment": {
            "provider_groups": sum(item["provider_groups"] for item in quality),
            "display_groups": sum(item["display_groups"] for item in quality),
            "suppressed_groups": sum(item["suppressed_groups"] for item in quality),
            "results_with_suppression": sum(item["suppressed_groups"] > 0 for item in quality),
            "median_source_character_coverage": (
                round(median(item["source_character_coverage"] for item in quality), 4)
                if quality
                else None
            ),
            "median_target_character_coverage": (
                round(median(item["target_character_coverage"] for item in quality), 4)
                if quality
                else None
            ),
            "max_source_tokens_per_group": max(
                (item["max_source_tokens_per_group"] for item in quality), default=0
            ),
            "groups_reactivated_over_four_cues": sum(
                item.get("playback_alignment", {}).get("groups_reactivated_over_four_cues", 0)
                for item in valid
            ),
        },
    }


def playback_alignment(groups, timing: list[dict[str, Any]]) -> dict[str, int]:
    cue_counts = [
        sum(
            any(
                source.start < int(cue["char_end"]) and source.end > int(cue["char_start"])
                for source in group.source_ranges
            )
            for cue in timing
        )
        for group in groups
    ]
    return {
        "display_groups": len(groups),
        "groups_spanning_multiple_cues": sum(count > 1 for count in cue_counts),
        "groups_reactivated_over_four_cues": sum(count > 4 for count in cue_counts),
        "max_cues_per_display_group": max(cue_counts, default=0),
    }


def failure_diagnostics(
    response: Any, request: ProviderTranslationRequest
) -> dict[str, Any] | None:
    """Retain compact structural evidence for invalid provider output."""
    if response is None or not isinstance(response.payload, dict):
        return None
    source_chunks = response.payload.get("source_chunks")
    target_chunks = response.payload.get("target_chunks")
    if not isinstance(source_chunks, list) or not isinstance(target_chunks, list):
        return None

    def ids(chunks: list[Any]) -> set[int]:
        return {
            item["group_id"]
            for item in chunks
            if isinstance(item, dict)
            and isinstance(item.get("group_id"), int)
            and item["group_id"] > 0
        }

    source_ids = ids(source_chunks)
    target_ids = ids(target_chunks)
    reconstructed = "".join(
        str(item.get("text", "")) for item in source_chunks if isinstance(item, dict)
    )
    return {
        "source_reconstructs_exactly": reconstructed == request.source_text,
        "source_only_group_ids": sorted(source_ids - target_ids),
        "target_only_group_ids": sorted(target_ids - source_ids),
    }


async def evaluate(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file, override=False)
    key = os.environ.get("SPEECH_RETRIEVAL_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY or SPEECH_RETRIEVAL_GEMINI_API_KEY")
    targets = tuple(dict.fromkeys(args.target))
    calls_per_source = len(targets) * args.repetitions
    source_count = max(1, (args.calls + calls_per_source - 1) // calls_per_source)
    selected = samples(
        args.data_dir / "index" / "corpus.sqlite3", source_count, args.segment_id or None
    )
    provider = GeminiTranslationProvider(key, args.model, timeout_seconds=args.timeout)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "output_schema_version": TRANSLATION_SCHEMA_VERSION,
        "targets": targets,
        "segment_ids": args.segment_id,
        "repetitions": args.repetitions,
        "requested_call_limit": args.calls,
        "requests_per_minute": args.rpm,
        "results": [],
    }
    delay = 60 / args.rpm
    last_started = 0.0
    try:
        for repetition in range(args.repetitions):
            for row in selected:
                for target in targets:
                    if len(report["results"]) >= args.calls:
                        break
                    elapsed = time.monotonic() - last_started
                    if last_started and elapsed < delay:
                        await asyncio.sleep(delay - elapsed)
                    last_started = time.monotonic()
                    request = ProviderTranslationRequest(
                        source_text=row["text"],
                        source_language=row["source_language"],
                        target_language=target,
                    )
                    timing = json.loads(row["segments_json"])
                    item: dict[str, Any] = {
                        **{key: value for key, value in row.items() if key != "segments_json"},
                        "source_timing": timing,
                        "target_language": target,
                        "repetition": repetition + 1,
                    }
                    response = None
                    try:
                        response = await provider.generate(request)
                        result = validate_provider_output(
                            response, request, provider=provider.provider, model=provider.model
                        )
                        aligned_target = sum(
                            value.end - value.start
                            for group in result.alignment_groups
                            for value in group.target_ranges
                        )
                        item.update(
                            {
                                "status": "valid",
                                "target_text": result.target_text,
                                "provider_chunks": response.payload,
                                "alignment_groups": [
                                    group.model_dump() for group in result.alignment_groups
                                ],
                                "alignment_quality": result.alignment_quality.model_dump()
                                if result.alignment_quality
                                else None,
                                "playback_alignment": playback_alignment(
                                    result.alignment_groups, timing
                                ),
                                "aligned_target_fraction": round(
                                    aligned_target / max(1, len(result.target_text)), 4
                                ),
                                "latency_ms": result.latency_ms,
                                "usage": result.usage,
                                "warnings": result.warnings,
                            }
                        )
                    except Exception as error:
                        item.update(
                            {
                                "status": "failed",
                                "error_type": type(error).__name__,
                                "error": str(error),
                                "provider_chunks": response.payload if response else None,
                                "failure_diagnostics": failure_diagnostics(response, request),
                            }
                        )
                    report["results"].append(item)
                    report["completed_calls"] = len(report["results"])
                    report["valid_calls"] = sum(
                        result["status"] == "valid" for result in report["results"]
                    )
                    report["summary"] = summarize(report["results"])
                    write_report(args.output, report)
                    print(
                        f"{len(report['results']):3}/{args.calls} {target} {item['status']} "
                        f"{row['segment_id']} repeat={repetition + 1}"
                    )
    finally:
        await provider.aclose()
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["summary"] = summarize(report["results"])
    write_report(args.output, report)
    return 0 if report.get("valid_calls") == report.get("completed_calls") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/experiments/target-language-text/live-evaluation.json"),
    )
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--segment-id", action="append", default=[])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--rpm", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not args.target:
        args.target = ["en", "ru"]
    if args.calls < 1 or args.repetitions < 1 or not 0 < args.rpm <= 60:
        parser.error("calls and rpm must be positive; rpm must not exceed 60")
    try:
        return asyncio.run(evaluate(args))
    except Exception as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
