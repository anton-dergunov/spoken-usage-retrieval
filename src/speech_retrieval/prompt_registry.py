from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class PromptSpec:
    resource: str
    version: str
    schema_version: int
    temperature: float

    @property
    def text(self) -> str:
        return files("speech_retrieval.prompts").joinpath(self.resource).read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


TRANSLATION_PROMPT = PromptSpec("translate_literal_v1.md", "literal-translation-v1", 1, 0.2)
ALIGNMENT_PROMPT = PromptSpec("align_words_v1.md", "fixed-token-alignment-v1", 1, 0.0)

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "target_text": {"type": "STRING"},
        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["target_text", "warnings"],
}


def alignment_schema(source_ids: list[str], target_ids: list[str]) -> dict[str, Any]:
    """Build a request-specific schema that constrains every token reference."""
    return {
        "type": "OBJECT",
        "properties": {
            "alignments": {
                "type": "ARRAY",
                "minItems": len(source_ids),
                "maxItems": len(source_ids),
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "source_id": {"type": "STRING", "enum": source_ids},
                        "target_ids": {
                            "type": "ARRAY",
                            "items": {"type": "STRING", "enum": target_ids},
                        },
                    },
                    "required": ["source_id", "target_ids"],
                },
            },
            "unaligned_target_ids": {
                "type": "ARRAY",
                "items": {"type": "STRING", "enum": target_ids},
            },
            "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["alignments", "unaligned_target_ids", "warnings"],
    }
