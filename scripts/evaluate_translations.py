#!/usr/bin/env python3
"""Exercise the production translation prompt against representative indexed clips.

This is deliberately excluded from CI because it spends provider calls. Results go under ignored
``data/experiments`` and contain no API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from speech_retrieval.translations import (
    GeminiTranslationProvider,
    ProviderTranslationRequest,
    validate_provider_output,
)


def samples(database: Path, count: int) -> list[dict[str, str]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT s.segment_id, s.source_language, s.text, s.boundary_reason,
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


async def evaluate(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file, override=False)
    key = os.environ.get("SPEECH_RETRIEVAL_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY or SPEECH_RETRIEVAL_GEMINI_API_KEY")
    targets = tuple(dict.fromkeys(args.target))
    source_count = max(1, (args.calls + len(targets) - 1) // len(targets))
    selected = samples(args.data_dir / "index" / "corpus.sqlite3", source_count)
    provider = GeminiTranslationProvider(key, args.model, timeout_seconds=args.timeout)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "targets": targets,
        "requested_call_limit": args.calls,
        "requests_per_minute": args.rpm,
        "results": [],
    }
    delay = 60 / args.rpm
    last_started = 0.0
    try:
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
                item: dict[str, Any] = {**row, "target_language": target}
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
                            "alignment_groups": len(result.alignment_groups),
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
                        }
                    )
                report["results"].append(item)
                report["completed_calls"] = len(report["results"])
                report["valid_calls"] = sum(
                    result["status"] == "valid" for result in report["results"]
                )
                write_report(args.output, report)
                print(
                    f"{len(report['results']):3}/{args.calls} {target} {item['status']} "
                    f"{row['segment_id']}"
                )
    finally:
        await provider.aclose()
    report["completed_at"] = datetime.now(UTC).isoformat()
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
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--rpm", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not args.target:
        args.target = ["en", "ru"]
    if args.calls < 1 or not 0 < args.rpm <= 60:
        parser.error("calls and rpm must be positive; rpm must not exceed 60")
    try:
        return asyncio.run(evaluate(args))
    except Exception as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
