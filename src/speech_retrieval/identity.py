from __future__ import annotations

import hashlib

CACHE_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 4  # videos.playable_in_embed
REPORT_SCHEMA_VERSION = 1
ANALYZER_ID = "unicode-regex-v1"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def video_key(provider: str, source_language: str, provider_video_id: str) -> str:
    return _stable_id("vid", provider, source_language, provider_video_id)


def track_id(video: str, kind: str, caption_language: str) -> str:
    return _stable_id("trk", video, kind, caption_language)


def clip_id(
    *,
    source_sha256: str,
    requested_start_ms: int,
    requested_end_ms: int,
    padding_ms: int,
    effective_start_ms: int,
    effective_end_ms: int,
    preparation_version: str,
) -> str:
    return _stable_id(
        "clp",
        source_sha256,
        str(requested_start_ms),
        str(requested_end_ms),
        str(padding_ms),
        str(effective_start_ms),
        str(effective_end_ms),
        preparation_version,
    )


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


def alignment_id(
    *,
    source_text: str,
    source_language: str,
    clip_content_sha256: str,
    aligner: str,
    model_id: str,
    settings_hash: str,
) -> str:
    """Identify one forced-alignment result by everything that could change it.

    Includes the model and its settings, so switching profiles produces a different row
    rather than silently reusing timing produced by another model -- which also makes rows
    from a non-commercial model findable and purgeable later.
    """
    text_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return _stable_id(
        "aln",
        text_hash,
        source_language,
        clip_content_sha256,
        aligner,
        model_id,
        settings_hash,
    )
