from __future__ import annotations

import hashlib

CACHE_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 3
REPORT_SCHEMA_VERSION = 1
ANALYZER_ID = "unicode-regex-v1"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def video_key(provider: str, source_language: str, provider_video_id: str) -> str:
    return _stable_id("vid", provider, source_language, provider_video_id)


def track_id(video: str, kind: str, caption_language: str) -> str:
    return _stable_id("trk", video, kind, caption_language)


def segment_id(
    *,
    provider_video_id: str,
    source_language: str,
    track: str,
    start: float,
    end: float,
    text: str,
) -> str:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _stable_id(
        "seg",
        provider_video_id,
        source_language,
        track,
        f"{start:.3f}",
        f"{end:.3f}",
        text_hash,
    )
