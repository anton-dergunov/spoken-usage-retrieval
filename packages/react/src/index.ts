export {
  SpeechClipPlayer,
  HighlightedSourceText,
  ProgressiveSourceText,
  formatClock,
  type SpeechClipPlayerError,
  type SpeechClipPlayerProps,
  type SpeechClipPlayerStatus,
} from "./SpeechClipPlayer.js";
export {
  createSpeechRetrievalClient,
  SpeechRetrievalApiError,
  type RequestOptions,
  type SearchOptions,
  type SpeechRetrievalClient,
  type SpeechRetrievalClientOptions,
  type SuggestionOptions,
} from "./client.js";
export type * from "./types.js";
export type { YouTubeApiLoader, YouTubeNamespace, YouTubePlayer } from "./youtube.js";
