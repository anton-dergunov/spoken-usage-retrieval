from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
TERMINAL_RE = re.compile(r"[.!?…][\]\)\"'»”’]*\s*$")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?…%\]\)])")
SPACE_AFTER_OPEN_RE = re.compile(r"([¿¡\[\(])\s+")


@dataclass(frozen=True)
class TextToken:
    text: str
    normalized: str
    start: int
    end: int


def fold_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def normalize_token(value: str) -> str:
    return fold_accents(value).replace("’", "'")


def tokens_with_spans(value: str) -> list[TextToken]:
    return [
        TextToken(
            text=match.group(0),
            normalized=normalize_token(match.group(0)),
            start=match.start(),
            end=match.end(),
        )
        for match in TOKEN_RE.finditer(value)
    ]


def normalized_query(value: str) -> tuple[str, list[TextToken]]:
    tokens = tokens_with_spans(value)
    return " ".join(token.normalized for token in tokens), tokens


def accent_key(value: str) -> str:
    return " ".join(token.text.casefold().replace("’", "'") for token in tokens_with_spans(value))


def clean_spacing(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", value)
    return SPACE_AFTER_OPEN_RE.sub(r"\1", value)


def join_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    if right[0] in ",.;:!?…%])}»”’" or left[-1] in "¿¡[({—-/":
        return clean_spacing(left + right)
    return clean_spacing(f"{left} {right}")
