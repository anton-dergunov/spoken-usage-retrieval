from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return self.model_dump(mode="python") == other
        return super().__eq__(other)


class AnalyzerInfo(ContractModel):
    name: str
    language: str
    package_version: str
    model_version: str | None
    settings: dict[str, Any]
    identity: str


class TokenInfo(ContractModel):
    surface: str
    normalized: str
    start: int
    end: int
    lemma: str | None = None
    upos: str | None = None
    features: dict[str, str] | None = None
    word: str | None = None
    shared_span: bool = False


class LemmaCandidate(ContractModel):
    lemma: str
    upos: str | None
    sources: list[str]
    frequency: int
    analyzer: AnalyzerInfo


class QueryAnalysis(ContractModel):
    position: int
    token: TokenInfo
    candidates: list[LemmaCandidate]
    ambiguous: bool


class MatchSpan(ContractModel):
    text: str
    char_start: int
    char_end: int
    accent_exact: bool


class TimedText(ContractModel):
    text: str
    start: float
    end: float
    char_start: int
    char_end: int


class BoundaryInfo(ContractModel):
    reason: str
    confidence: float


class VideoInfo(ContractModel):
    video_key: str
    provider: str
    id: str
    url: str
    title: str
    channel_id: str | None
    channel: str
    source_language: str
    varieties: list[str]
    speech_style: list[str]
    duration: float | None
    thumbnail: str | None
    track_id: str
    caption_kind: str
    caption_language: str
    caption_track_kind: Literal["authored", "automatic"] | None = None
    caption_provider_track_id: str | None = None
    caption_is_source: bool = True


class SearchResult(ContractModel):
    occurrence_id: str
    segment_id: str
    source_language: str
    sentence: str
    match_type: Literal["exact", "lemma"]
    matched_surface: str
    matched_lemma: str | None
    token_analysis: list[TokenInfo]
    analyzer: AnalyzerInfo
    match: MatchSpan
    sentence_start: float
    sentence_end: float
    clip_start: float
    clip_end: float
    segments: list[TimedText]
    boundary: BoundaryInfo
    quality_score: float
    score: float
    rank: int
    video: VideoInfo


class SearchResponse(ContractModel):
    query: str
    normalized_query: str
    source_language: str
    match_mode: Literal["auto", "exact", "lemma"]
    order: Literal["ranked", "random"]
    seed: int | None = Field(description="Effective random seed, or null for ranked ordering.")
    morphology_available: bool
    morphology_unavailable_reason: str | None
    query_analyses: list[QueryAnalysis]
    query_analyzer: AnalyzerInfo
    totals_by_mode: dict[str, int]
    total_occurrences: int
    returned: int
    results: list[SearchResult]


class Suggestion(ContractModel):
    source_language: str
    text: str
    normalized: str
    size: int
    occurrences: int
    videos: int


class SuggestionsResponse(ContractModel):
    source_language: str
    suggestions: list[Suggestion]


class Clip(ContractModel):
    segment_id: str
    source_language: str
    source_text: str
    sentence_start: float
    sentence_end: float
    clip_start: float
    clip_end: float
    segments: list[TimedText]
    boundary: BoundaryInfo
    quality_score: float
    analyzer: AnalyzerInfo
    video: VideoInfo
    target_language: str | None = Field(
        default=None, description="Reserved for the optional translation capability."
    )
    target_text: str | None = Field(
        default=None, description="Reserved for the optional translation capability."
    )
    translation_provenance: dict[str, Any] | None = Field(
        default=None, description="Reserved for the optional translation capability."
    )
    alignment_status: Literal["unavailable"] = Field(
        default="unavailable",
        description="Unavailable until the optional alignment capability is installed.",
    )
    alignment_coverage: float | None = Field(
        default=None, description="Reserved for the optional alignment capability."
    )
    alignment_provenance: dict[str, Any] | None = Field(
        default=None, description="Reserved for the optional alignment capability."
    )
    alignment_groups: list[dict[str, Any]] | None = Field(
        default=None, description="Reserved for the optional alignment capability."
    )


TranslationState = Literal[
    "not_requested",
    "queued",
    "running",
    "complete",
    "failed",
    "cancelled",
    "interrupted",
    "unavailable",
]


class CharacterRange(ContractModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SemanticAlignmentGroup(ContractModel):
    group_id: int = Field(ge=1)
    source_ranges: list[CharacterRange]
    target_ranges: list[CharacterRange]


class TranslationResult(ContractModel):
    source_language: str
    target_language: str
    source_text_hash: str
    target_text: str
    alignment_groups: list[SemanticAlignmentGroup]
    provenance: Literal["llm", "authored_track"]
    provider: str
    model: str | None
    prompt_version: str
    schema_version: int
    authored_track_language: str | None = None
    authored_track_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    usage: dict[str, int] | None = None
    provider_metadata: dict[str, str] | None = None


class TranslationErrorInfo(ContractModel):
    code: str
    message: str
    retryable: bool = False


class TranslationRequest(ContractModel):
    target_language: str


class TranslationJob(ContractModel):
    job_id: str
    segment_id: str
    target_language: str
    status: TranslationState
    cache_hit: bool = False
    result: TranslationResult | None = None
    error: TranslationErrorInfo | None = None
    created_at: str
    updated_at: str


class TranslationBatchRequest(ContractModel):
    segment_ids: list[str] = Field(min_length=1, max_length=50)
    target_language: str


class TranslationBatchItem(ContractModel):
    segment_id: str
    job_id: str
    status: TranslationState
    cache_hit: bool


class TranslationBatchCounts(ContractModel):
    total: int = 0
    cached: int = 0
    queued: int = 0
    running: int = 0
    complete: int = 0
    failed: int = 0
    cancelled: int = 0
    interrupted: int = 0
    unavailable: int = 0


class TranslationBatch(ContractModel):
    batch_id: str
    target_language: str
    total: int
    counts: TranslationBatchCounts
    jobs: list[TranslationBatchItem]
    created_at: str
    updated_at: str


class TranslationCacheStatistics(ContractModel):
    completed_entries: int = 0
    failed_entries: int = 0
    invalid_entries: int = 0
    hits: int = 0
    misses: int = 0
    active_jobs: int = 0
    database_bytes: int = 0
    concurrency: int = 0


class TranslationServiceStatus(ContractModel):
    provider_available: bool = False
    provider: str | None = None
    model: str | None = None
    target_languages: list[str] = Field(default_factory=list)
    default_target_language: str | None = None
    cache: TranslationCacheStatistics = Field(default_factory=TranslationCacheStatistics)


class LanguageStatus(ContractModel):
    source_language: str
    configured: bool
    enabled: bool
    indexed: bool
    configured_channels: int
    enabled_channels: int
    videos: int
    segments: int
    occurrences: int
    caption_kinds: dict[str, int]
    analyzer_id: str | None
    analyzer: AnalyzerInfo | None
    morphology_available: bool


class CorpusStatus(ContractModel):
    ready: bool
    error: str | None = None
    package_version: str
    database_schema_version: int | None
    built_at: str | None
    max_ngram: int | None
    analyzer_selection: str | None
    analyzer_id: str | None
    configured_languages: list[str]
    enabled_languages: list[str]
    indexed_languages: list[str]
    languages: list[LanguageStatus]
    videos: int
    segments: int
    occurrences: int
    caption_kinds: dict[str, int]
    channel_mutations_enabled: bool = False
    translation: TranslationServiceStatus = Field(default_factory=TranslationServiceStatus)


class ChannelRecord(ContractModel):
    source_language: str
    section_id: str
    section_name: str
    id: str
    name: str
    url: str
    enabled: bool
    varieties: list[str] | None = None
    speech_style: list[str] | None = None
    description: str | None = None


class ChannelCreate(ContractModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "source_language": "es",
                "section_id": "learning_and_language",
                "id": "example-channel",
                "name": "Example Channel",
                "url": "https://www.youtube.com/@example/videos",
                "enabled": False,
            }
        },
    )

    source_language: str
    section_id: str
    id: str
    name: str
    url: str
    enabled: bool = False
    varieties: list[str] | None = None
    speech_style: list[str] | None = None
    description: str | None = None


class ChannelUpdate(ContractModel):
    name: str | None = None
    url: str | None = None
    varieties: list[str] | None = None
    speech_style: list[str] | None = None
    description: str | None = None


class FailureRecord(ContractModel):
    occurred_at: str
    operation: str
    source_language: str | None = None
    channel: str | None = None
    item: str | None = None
    message: str


class Activity(ContractModel):
    operation: str
    started_at: str
    pid: int


class ChannelStatistics(ContractModel):
    source_language: str
    channel_id: str
    channel_name: str
    configured: bool
    enabled: bool
    videos: int
    segments: int
    caption_kinds: dict[str, int]


class LanguageStatistics(ContractModel):
    source_language: str
    videos: int
    segments: int
    caption_kinds: dict[str, int]


class CorpusStatistics(ContractModel):
    generated_at: str
    videos: int
    segments: int
    caption_kinds: dict[str, int]
    languages: list[LanguageStatistics]
    channels: list[ChannelStatistics]
    last_successful_update: str | None
    current_activity: Activity | None
    recent_failures: list[FailureRecord]
    translation_cache: TranslationCacheStatistics = Field(
        default_factory=TranslationCacheStatistics
    )


class LanguageUpdate(ContractModel):
    source_language: str
    downloaded: int
    cached: int
    failures: int
    complete: bool


class UpdateSummary(ContractModel):
    started_at: str
    completed_at: str
    successful: bool
    downloaded: int
    cached: int
    failures: int
    languages: list[LanguageUpdate]
    index: dict[str, Any] | None


class DoctorCheck(ContractModel):
    name: str
    status: Literal["ok", "warning", "error"]
    message: str


class DoctorReport(ContractModel):
    healthy: bool
    checks: list[DoctorCheck]


class ModelRecord(ContractModel):
    language: str
    installed: bool
    processors: list[str]


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(ContractModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "error": {"code": "invalid_request", "message": "Invalid source language"},
                "request_id": "9c93f7ad-caca-42b8-bb2f-0fa4ced8e06e",
            }
        },
    )

    error: ErrorDetail
    request_id: str
