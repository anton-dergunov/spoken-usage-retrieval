"""Run the manually invoked multilingual translation/alignment evaluation.

Artifacts are written below ignored ``data/experiments``. The script never records the API key.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from dotenv import load_dotenv

from speech_retrieval.contracts import AlignmentToken, CharacterRange
from speech_retrieval.prompt_registry import (
    ALIGNMENT_PROMPT,
    TRANSLATION_PROMPT,
    TRANSLATION_SCHEMA,
    alignment_schema,
)
from speech_retrieval.text import tokens_with_spans
from speech_retrieval.translations import (
    GeminiTranslationProvider,
    ProviderAlignmentRequest,
    ProviderResponse,
    ProviderTranslationRequest,
    validate_alignment_output,
    validate_translation_output,
)

ROOT = Path(__file__).parent
DEFAULT_OUTPUT_DIR = Path("data/experiments/target-language-word-alignment")


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ROOT / "challenge-set-v1.jsonl").read_text().splitlines()]


def tokens(values: list[dict[str, Any]]) -> tuple[AlignmentToken, ...]:
    return tuple(AlignmentToken.model_validate(value) for value in values)


def generated_tokens(text: str, prefix: str = "T") -> tuple[AlignmentToken, ...]:
    return tuple(
        AlignmentToken(
            id=f"{prefix}{index}",
            text=token.text,
            range=CharacterRange(start=token.start, end=token.end),
        )
        for index, token in enumerate(tokens_with_spans(text), 1)
    )


def edge_schema(source_ids: list[str], target_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "edges": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "source_id": {"type": "STRING", "enum": source_ids},
                        "target_id": {"type": "STRING", "enum": target_ids},
                    },
                    "required": ["source_id", "target_id"],
                },
            },
            "unaligned_source_ids": {
                "type": "ARRAY",
                "items": {"type": "STRING", "enum": source_ids},
            },
            "unaligned_target_ids": {
                "type": "ARRAY",
                "items": {"type": "STRING", "enum": target_ids},
            },
            "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["edges", "unaligned_source_ids", "unaligned_target_ids", "warnings"],
    }


EDGE_PROMPT = """Align the immutable labeled lexical tokens in the two supplied sentences.
Return semantic links as individual source_id/target_id edges. Repetition occurrences are distinct.
Many-to-many, crossing, and noncontiguous links are legal. Account for every unlinked source and
target ID in the respective unaligned list. Never rewrite, renumber, merge, or invent tokens."""

CONSERVATIVE_SUFFIX = """
Prefer an honest unaligned token to a speculative edge. Link grammatical function words only when
the other language expresses that function lexically. For multiword expressions, emit all direct
token links needed to preserve the semantic correspondence."""


def adjacency_from_edges(payload: dict[str, Any], source_ids: list[str]) -> dict[str, Any]:
    rows: dict[str, list[str]] = {source_id: [] for source_id in source_ids}
    for edge in payload.get("edges", []):
        if not isinstance(edge, dict) or edge.get("source_id") not in rows:
            raise ValueError("invalid edge row")
        target_id = edge.get("target_id")
        if not isinstance(target_id, str):
            raise ValueError("invalid target ID")
        rows[edge["source_id"]].append(target_id)
    declared = payload.get("unaligned_source_ids")
    if not isinstance(declared, list) or set(declared) != {
        key for key, value in rows.items() if not value
    }:
        raise ValueError("source accounting mismatch")
    return {
        "alignments": [{"source_id": key, "target_ids": value} for key, value in rows.items()],
        "unaligned_target_ids": payload.get("unaligned_target_ids"),
        "warnings": payload.get("warnings", []),
    }


def prompt_input(
    case: dict[str, Any],
    source: tuple[AlignmentToken, ...],
    target: tuple[AlignmentToken, ...],
    target_text: str,
) -> str:
    def lines(items: tuple[AlignmentToken, ...]) -> str:
        return "\n".join(
            f"{item.id}: {json.dumps(item.text, ensure_ascii=False)}" for item in items
        )

    return (
        f"Source language: {case['source_language']}\nTarget language: en\n"
        f"Source text: {case['source_text']}\nTarget text: {target_text}\n\n"
        f"Source tokens:\n{lines(source)}\n\nTarget tokens:\n{lines(target)}"
    )


def alignment_metrics(predicted: set[tuple[str, str]], case: dict[str, Any]) -> dict[str, float]:
    sure = {tuple(edge) for edge in case["sure_links"]}
    possible = sure | {tuple(edge) for edge in case["possible_links"]}
    correct = len(predicted & sure)
    precision = len(predicted & possible) / max(1, len(predicted))
    recall = correct / max(1, len(sure))
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    aer = 1 - (len(predicted & sure) + len(predicted & possible)) / max(
        1, len(predicted) + len(sure)
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "aer": aer,
        "predicted": len(predicted),
        "sure": len(sure),
        "correct": correct,
    }


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in items if item.get("structurally_valid")]
    scored = [item for item in valid if "metrics" in item]
    latencies = [item["latency_ms"] for item in items if item.get("latency_ms") is not None]
    total_predicted = sum(item["metrics"]["predicted"] for item in scored)
    total_sure = sum(item["metrics"]["sure"] for item in scored)
    total_correct = sum(item["metrics"]["correct"] for item in scored)
    micro_precision = total_correct / max(1, total_predicted)
    micro_recall = total_correct / max(1, total_sure)
    micro_f1 = 2 * micro_precision * micro_recall / max(1e-12, micro_precision + micro_recall)
    summary = {
        "attempts": len(items),
        "structurally_valid": len(valid),
        "structural_validity": len(valid) / max(1, len(items)),
        "median_latency_ms": median(latencies) if latencies else None,
        "tokens": sum((item.get("usage") or {}).get("total_tokens", 0) for item in items),
    }
    if scored:
        summary.update(
            {
                "micro_precision": micro_precision,
                "micro_recall": micro_recall,
                "micro_f1": micro_f1,
                "macro_f1": mean(item["metrics"]["f1"] for item in scored),
                "macro_aer": mean(item["metrics"]["aer"] for item in scored),
            }
        )
    return summary


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class Runner:
    def __init__(self, provider: GeminiTranslationProvider, output: Path, rpm: float):
        self.provider = provider
        self.output = output
        self.delay = 60 / rpm
        self.last_call = 0.0
        self.report: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "model": provider.model,
            "challenge_sha256": hashlib.sha256(
                (ROOT / "challenge-set-v1.jsonl").read_bytes()
            ).hexdigest(),
            "translation_prompt": {
                "version": TRANSLATION_PROMPT.version,
                "sha256": TRANSLATION_PROMPT.sha256,
            },
            "alignment_prompt": {
                "version": ALIGNMENT_PROMPT.version,
                "sha256": ALIGNMENT_PROMPT.sha256,
            },
            "attempts": [],
        }

    async def paced(self, operation, **kwargs) -> ProviderResponse:
        elapsed = time.monotonic() - self.last_call
        if self.last_call and elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed)
        self.last_call = time.monotonic()
        return await operation(**kwargs)

    def record(self, item: dict[str, Any]) -> None:
        self.report["attempts"].append(item)
        self.report["call_count"] = len(self.report["attempts"])
        write_report(self.output, self.report)

    async def align_case(
        self,
        case: dict[str, Any],
        phase: str,
        variant: str,
        *,
        target_text: str | None = None,
        target_override=None,
    ) -> None:
        source = tokens(case["source_tokens"])
        target = target_override or tokens(case["target_tokens"])
        target_text = target_text or case["target_text"]
        request = ProviderAlignmentRequest(
            case["source_text"], target_text, case["source_language"], "en", source, target
        )
        if variant == "edges":
            instructions = EDGE_PROMPT
            schema = edge_schema([item.id for item in source], [item.id for item in target])
        else:
            instructions = ALIGNMENT_PROMPT.text + (
                CONSERVATIVE_SUFFIX if variant == "conservative" else ""
            )
            schema = alignment_schema([item.id for item in source], [item.id for item in target])
        item: dict[str, Any] = {
            "phase": phase,
            "variant": variant,
            "case_id": case["id"],
            "language": case["source_language"],
            "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "schema": schema,
            "source_tokens": case["source_tokens"],
            "target_tokens": [value.model_dump(mode="json") for value in target],
        }
        try:
            response = await self.paced(
                self.provider._generate,
                instructions=instructions,
                user_text=prompt_input(case, source, target, target_text),
                schema=schema,
                temperature=0.0,
            )
            payload = (
                adjacency_from_edges(response.payload, [value.id for value in source])
                if variant == "edges"
                else response.payload
            )
            result = validate_alignment_output(
                ProviderResponse(
                    payload,
                    response.latency_ms,
                    response.usage,
                    response.raw_output,
                    response.provider_metadata,
                ),
                request,
            )
            predicted = {
                (edge.source_token_id, edge.target_token_id) for edge in result.graph.edges
            }
            item.update(
                {
                    "structurally_valid": True,
                    "raw_output": response.raw_output,
                    "normalized_graph": result.graph.model_dump(mode="json"),
                    "latency_ms": response.latency_ms,
                    "usage": response.usage,
                    "provider_metadata": response.provider_metadata,
                }
            )
            if target_override is None:
                item["metrics"] = alignment_metrics(predicted, case)
        except Exception as error:
            item.update(
                {
                    "structurally_valid": False,
                    "validation_error": f"{type(error).__name__}: {error}",
                }
            )
            if "response" in locals():
                item.update(
                    {
                        "raw_output": response.raw_output,
                        "latency_ms": response.latency_ms,
                        "usage": response.usage,
                        "provider_metadata": response.provider_metadata,
                    }
                )
        self.record(item)

    async def translate_case(self, case: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        request = ProviderTranslationRequest(case["source_text"], case["source_language"], "en")
        item: dict[str, Any] = {
            "phase": "pipeline-translation",
            "variant": "production",
            "case_id": case["id"],
            "language": case["source_language"],
            "prompt_sha256": TRANSLATION_PROMPT.sha256,
            "schema": TRANSLATION_SCHEMA,
        }
        try:
            response = await self.paced(self.provider.translate, request=request)
            result = validate_translation_output(response)
            from sacrebleu.metrics import CHRF

            score = (
                CHRF(word_order=2).sentence_score(result.target_text, [case["target_text"]]).score
            )
            item.update(
                {
                    "structurally_valid": True,
                    "target_text": result.target_text,
                    "chrf_pp": score,
                    "warnings": result.warnings,
                    "raw_output": response.raw_output,
                    "latency_ms": response.latency_ms,
                    "usage": response.usage,
                    "provider_metadata": response.provider_metadata,
                }
            )
            self.record(item)
            return result.target_text, item
        except Exception as error:
            item.update(
                {
                    "structurally_valid": False,
                    "validation_error": f"{type(error).__name__}: {error}",
                }
            )
            self.record(item)
            return None, item


async def run(args: argparse.Namespace) -> None:
    load_dotenv(args.env_file, override=False)
    key = os.environ.get("SPEECH_RETRIEVAL_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY or SPEECH_RETRIEVAL_GEMINI_API_KEY")
    provider = GeminiTranslationProvider(key, args.model, timeout_seconds=args.timeout)
    runner = Runner(provider, args.output, args.rpm)
    cases = load_cases()
    if args.case_id:
        cases = [case for case in cases if case["id"] in set(args.case_id)]
    dev = [case for case in cases if case["split"] == "dev"]
    heldout = [case for case in cases if case["split"] == "test"]
    try:
        if args.phase in {"pilot", "all"}:
            for variant in args.variant or ("edges", "adjacency", "conservative"):
                for case in dev:
                    await runner.align_case(case, "pilot", variant)
        if args.phase in {"heldout", "all"}:
            for case in heldout:
                await runner.align_case(case, "heldout", "adjacency")
        if args.phase in {"pipeline", "all"}:
            for case in cases:
                translated, _ = await runner.translate_case(case)
                if translated:
                    target = generated_tokens(translated)
                    if target:
                        await runner.align_case(
                            case,
                            "pipeline-alignment",
                            "adjacency",
                            target_text=translated,
                            target_override=target,
                        )
        if args.phase in {"stability", "all"}:
            difficult = [
                case
                for case in heldout
                if {"repetition", "discontinuous", "many-to-many"} & set(case["phenomena"])
            ][:10]
            for case in difficult:
                for _ in range(2):
                    await runner.align_case(case, "stability", "adjacency")
    finally:
        await provider.aclose()
    phases = defaultdict(list)
    for attempt in runner.report["attempts"]:
        phases[attempt["phase"]].append(attempt)
    runner.report["completed_at"] = datetime.now(UTC).isoformat()
    runner.report["summary"] = {phase: aggregate(items) for phase, items in phases.items()}
    runner.report["by_language"] = {
        language: aggregate(
            [item for item in runner.report["attempts"] if item["language"] == language]
        )
        for language in sorted({case["source_language"] for case in cases})
    }
    runner.report["phenomenon_counts"] = Counter(tag for case in cases for tag in case["phenomena"])
    write_report(args.output, runner.report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=["pilot", "heldout", "pipeline", "stability", "all"], default="all"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output",
        type=Path,
        help="Artifact path (defaults to data/experiments/target-language-word-alignment/<phase>.json)",
    )
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--rpm", type=float, default=20)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--variant", action="append", choices=["edges", "adjacency", "conservative"], default=[]
    )
    args = parser.parse_args()
    if not 0 < args.rpm <= 60:
        parser.error("rpm must be between 0 and 60")
    if args.output is None:
        args.output = DEFAULT_OUTPUT_DIR / f"{args.phase}.json"
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
