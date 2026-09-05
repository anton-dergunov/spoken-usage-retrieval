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
  caption_kind: "manual" | "automatic";
  caption_language: string;
  track_id: string;
}

export interface SearchResult {
  occurrence_id: string;
  segment_id: string;
  source_language: string;
  sentence: string;
  match: {
    text: string;
    char_start: number;
    char_end: number;
    accent_exact: boolean;
  };
  sentence_start: number;
  sentence_end: number;
  clip_start: number;
  clip_end: number;
  segments: Array<{
    text: string;
    start: number;
    end: number;
    char_start: number;
    char_end: number;
  }>;
  boundary: { reason: "punctuation" | "pause" | "forced" | "end"; confidence: number };
  quality_score: number;
  video: VideoSource;
}

export interface SearchResponse {
  query: string;
  normalized_query: string;
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

export interface CorpusStatus {
  ready: boolean;
  package_version: string;
  database_schema_version: number;
  built_at: string;
  max_ngram: number;
  analyzer_id: string;
  configured_languages: string[];
  enabled_languages: string[];
  indexed_languages: string[];
  languages: Array<{
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
    analyzer_id: string;
  }>;
  videos: number;
  segments: number;
  occurrences: number;
  caption_kinds: Record<string, number>;
}
