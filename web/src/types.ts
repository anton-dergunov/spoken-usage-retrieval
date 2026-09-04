export interface VideoSource {
  provider: "youtube" | string;
  id: string;
  url: string;
  title: string;
  channel_id: string | null;
  channel: string;
  varieties: string[];
  speech_style: string[];
  duration: number | null;
  thumbnail: string | null;
  caption_kind: "manual" | "automatic";
  caption_language: string;
}

export interface SearchResult {
  occurrence_id: string;
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
  boundary: { reason: "punctuation" | "pause" | "forced" | "end"; confidence: number };
  quality_score: number;
  video: VideoSource;
}

export interface SearchResponse {
  query: string;
  normalized_query: string;
  total_occurrences: number;
  returned: number;
  results: SearchResult[];
}

export interface Suggestion {
  text: string;
  normalized: string;
  size: number;
  occurrences: number;
  videos: number;
}

export interface CorpusStatus {
  ready: boolean;
  version: string;
  built_at: string;
  max_ngram: number;
  videos: number;
  segments: number;
  occurrences: number;
  caption_kinds: Record<string, number>;
}

