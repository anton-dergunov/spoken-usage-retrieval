export type MatchMode = "auto" | "exact" | "lemma";
export type SearchOrder = "ranked" | "random";

export interface AnalyzerProvenance {
  name: string;
  language: string;
  package_version: string;
  model_version: string | null;
  settings: Record<string, unknown>;
  identity: string;
}

export interface AnalyzedToken {
  surface: string;
  normalized: string;
  start: number;
  end: number;
  lemma: string | null;
  upos: string | null;
  features: Record<string, string> | null;
  word: string | null;
  shared_span: boolean;
}

export interface MatchSpan {
  text: string;
  char_start: number;
  char_end: number;
  accent_exact: boolean;
}

export interface TimedText {
  text: string;
  start: number;
  end: number;
  char_start: number;
  char_end: number;
}

export interface VideoSource {
  video_key: string;
  provider: "youtube" | string;
  id: string;
  url: string;
  title: string;
  channel_id: string | null;
  channel: string;
  source_language: string;
  varieties: string[];
  speech_style: string[];
  duration: number | null;
  thumbnail: string | null;
  caption_kind: "manual" | "automatic" | string;
  caption_language: string;
  track_id: string;
  caption_track_kind?: "authored" | "automatic" | null;
  caption_provider_track_id?: string | null;
  caption_is_source?: boolean;
}

export interface SearchResult {
  occurrence_id: string;
  segment_id: string;
  source_language: string;
  sentence: string;
  match_type: "exact" | "lemma";
  matched_surface: string;
  matched_lemma: string | null;
  token_analysis: AnalyzedToken[];
  analyzer: AnalyzerProvenance;
  match: MatchSpan;
  sentence_start: number;
  sentence_end: number;
  clip_start: number;
  clip_end: number;
  segments: TimedText[];
  boundary: { reason: string; confidence: number };
  quality_score: number;
  score: number;
  rank: number;
  video: VideoSource;
}

export interface SpeechClip {
  segment_id: string;
  source_language: string;
  source_text: string;
  sentence_start: number;
  sentence_end: number;
  clip_start: number;
  clip_end: number;
  segments: TimedText[];
  boundary: { reason: string; confidence: number };
  quality_score: number;
  analyzer: AnalyzerProvenance;
  video: VideoSource;
  target_language: string | null;
  target_text: string | null;
  translation_provenance: Record<string, unknown> | null;
  alignment_status: "unavailable" | string;
  alignment_coverage: number | null;
  alignment_provenance: Record<string, unknown> | null;
  alignment_groups: AlignmentGroup[] | null;
}

export interface AlignmentGroup {
  group_id: number;
  source_ranges: CharacterRange[];
  target_ranges: CharacterRange[];
}

export interface CharacterRange { start: number; end: number }

export type TranslationState =
  | "not_requested" | "queued" | "running" | "complete" | "failed"
  | "cancelled" | "interrupted" | "unavailable";

export interface TranslationResult {
  source_language: string;
  target_language: string;
  source_text_hash: string;
  target_text: string;
  alignment_groups: AlignmentGroup[];
  provenance: "llm" | "authored_track";
  provider: string;
  model: string | null;
  prompt_version: string;
  schema_version: number;
  authored_track_language: string | null;
  authored_track_id: string | null;
  warnings: string[];
  latency_ms: number | null;
  usage: Record<string, number> | null;
  provider_metadata: Record<string, string> | null;
}

export interface TranslationJob {
  job_id: string;
  segment_id: string;
  target_language: string;
  status: TranslationState;
  cache_hit: boolean;
  result: TranslationResult | null;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  updated_at: string;
}

export interface TranslationBatch {
  batch_id: string;
  target_language: string;
  total: number;
  counts: TranslationBatchCounts;
  jobs: Array<{ segment_id: string; job_id: string; status: TranslationState; cache_hit: boolean }>;
  created_at: string;
  updated_at: string;
}

export interface TranslationBatchCounts {
  total: number;
  cached: number;
  queued: number;
  running: number;
  complete: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  unavailable: number;
}

export interface TranslationCacheStatistics {
  completed_entries: number;
  failed_entries: number;
  invalid_entries: number;
  hits: number;
  misses: number;
  active_jobs: number;
  database_bytes: number;
  concurrency: number;
}

export interface TranslationServiceStatus {
  provider_available: boolean;
  provider: string | null;
  model: string | null;
  target_languages: string[];
  default_target_language: string | null;
  cache: TranslationCacheStatistics;
}

export type SpeechClipPlayerClip = SpeechClip | SearchResult;

export interface SearchResponse {
  query: string;
  normalized_query: string;
  match_mode: MatchMode;
  order: SearchOrder;
  seed: number | null;
  morphology_available: boolean;
  morphology_unavailable_reason: string | null;
  totals_by_mode: Record<MatchMode, number>;
  query_analyzer: AnalyzerProvenance;
  query_analyses: Array<{
    position: number;
    token: AnalyzedToken;
    ambiguous: boolean;
    candidates: Array<{
      lemma: string;
      upos: string | null;
      sources: string[];
      frequency: number;
      analyzer: AnalyzerProvenance;
    }>;
  }>;
  source_language: string;
  total_occurrences: number;
  returned: number;
  results: SearchResult[];
}

export interface Suggestion {
  source_language: string;
  text: string;
  normalized: string;
  size: number;
  occurrences: number;
  videos: number;
}

export interface SuggestionsResponse {
  source_language: string;
  suggestions: Suggestion[];
}

export interface LanguageStatus {
  source_language: string;
  configured: boolean;
  enabled: boolean;
  indexed: boolean;
  configured_channels: number;
  enabled_channels: number;
  videos: number;
  segments: number;
  occurrences: number;
  caption_kinds: Record<string, number>;
  analyzer_id: string | null;
  analyzer: AnalyzerProvenance | null;
  morphology_available: boolean;
}

export interface CorpusStatus {
  ready: boolean;
  error: string | null;
  package_version: string;
  database_schema_version: number | null;
  built_at: string | null;
  max_ngram: number | null;
  analyzer_selection: string | null;
  analyzer_id: string | null;
  configured_languages: string[];
  enabled_languages: string[];
  indexed_languages: string[];
  languages: LanguageStatus[];
  videos: number;
  segments: number;
  occurrences: number;
  caption_kinds: Record<string, number>;
  channel_mutations_enabled: boolean;
  translation: TranslationServiceStatus;
}

export interface ChannelRecord {
  source_language: string;
  section_id: string;
  section_name: string;
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  varieties: string[] | null;
  speech_style: string[] | null;
  description: string | null;
}

export interface ChannelCreate {
  source_language: string;
  section_id: string;
  id: string;
  name: string;
  url: string;
  enabled?: boolean;
  varieties?: string[] | null;
  speech_style?: string[] | null;
  description?: string | null;
}

export interface ChannelUpdate {
  name?: string | null;
  url?: string | null;
  varieties?: string[] | null;
  speech_style?: string[] | null;
  description?: string | null;
}

export interface CorpusStatistics {
  generated_at: string;
  videos: number;
  segments: number;
  caption_kinds: Record<string, number>;
  languages: Array<{
    source_language: string;
    videos: number;
    segments: number;
    caption_kinds: Record<string, number>;
  }>;
  channels: Array<{
    source_language: string;
    channel_id: string;
    channel_name: string;
    configured: boolean;
    enabled: boolean;
    videos: number;
    segments: number;
    caption_kinds: Record<string, number>;
  }>;
  last_successful_update: string | null;
  current_activity: { operation: string; started_at: string; pid: number } | null;
  recent_failures: Array<{
    occurred_at: string;
    operation: string;
    source_language: string | null;
    channel: string | null;
    item: string | null;
    message: string;
  }>;
  translation_cache: TranslationCacheStatistics;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
  request_id: string;
}

export interface LiveHealth {
  status: "live";
  package_version: string;
}

export interface ReadyHealth {
  status: "ready";
  built_at: string | null;
}
