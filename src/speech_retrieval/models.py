from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TimedUnit:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class TimedTextSegment:
    text: str
    start: float
    end: float
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Segment:
    id: str
    video_key: str
    video_id: str
    source_language: str
    track_id: str
    text: str
    start: float
    end: float
    clip_start: float
    clip_end: float
    boundary_reason: str
    boundary_confidence: float
    quality_score: float
    token_count: int
    segments: tuple[TimedTextSegment, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
