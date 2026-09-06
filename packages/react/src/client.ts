import type {
  ApiErrorBody,
  ChannelCreate,
  ChannelRecord,
  ChannelUpdate,
  CorpusStatistics,
  CorpusStatus,
  LiveHealth,
  MatchMode,
  SearchOrder,
  SearchResponse,
  SpeechClip,
  Suggestion,
  SuggestionsResponse,
  TranslationBatch,
  TranslationJob,
  ReadyHealth,
} from "./types.js";

export interface SpeechRetrievalClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
  operatorToken?: string;
}

export interface RequestOptions { signal?: AbortSignal }

export interface SearchOptions extends RequestOptions {
  query: string;
  language: string;
  matchMode?: MatchMode;
  order?: SearchOrder;
  limit?: number;
  seed?: number;
}

export interface SuggestionOptions extends RequestOptions {
  language: string;
  limit?: number;
}

export interface TranslationRequestOptions extends RequestOptions {
  targetLanguage: string;
}

export interface TranslationBatchOptions extends TranslationRequestOptions {
  segmentIds: string[];
}

export class SpeechRetrievalApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(response: Response, body: Partial<ApiErrorBody>) {
    super(body.error?.message ?? `Request failed (${response.status})`);
    this.name = "SpeechRetrievalApiError";
    this.status = response.status;
    this.code = body.error?.code ?? "http_error";
    this.requestId = body.request_id ?? response.headers.get("X-Request-ID");
    this.details = body.error?.details;
  }
}

export interface SpeechRetrievalClient {
  search(options: SearchOptions): Promise<SearchResponse>;
  suggestions(options: SuggestionOptions): Promise<Suggestion[]>;
  clip(segmentId: string, options?: RequestOptions): Promise<SpeechClip>;
  requestTranslation(segmentId: string, options: TranslationRequestOptions): Promise<TranslationJob>;
  translation(jobId: string, options?: RequestOptions): Promise<TranslationJob>;
  cancelTranslation(jobId: string, options?: RequestOptions): Promise<TranslationJob>;
  createTranslationBatch(options: TranslationBatchOptions): Promise<TranslationBatch>;
  translationBatch(batchId: string, options?: RequestOptions): Promise<TranslationBatch>;
  cancelTranslationBatch(batchId: string, options?: RequestOptions): Promise<TranslationBatch>;
  status(options?: RequestOptions): Promise<CorpusStatus>;
  statistics(options?: RequestOptions): Promise<CorpusStatistics>;
  live(options?: RequestOptions): Promise<LiveHealth>;
  ready(options?: RequestOptions): Promise<ReadyHealth>;
  channels(language?: string, options?: RequestOptions): Promise<ChannelRecord[]>;
  addChannel(channel: ChannelCreate, options?: RequestOptions): Promise<ChannelRecord>;
  updateChannel(language: string, channelId: string, update: ChannelUpdate, options?: RequestOptions): Promise<ChannelRecord>;
  setChannelEnabled(language: string, channelId: string, enabled: boolean, options?: RequestOptions): Promise<ChannelRecord>;
}

function normalizedBaseUrl(baseUrl: string): string {
  const value = baseUrl.trim().replace(/\/+$/, "");
  if (!value) throw new TypeError("baseUrl must not be empty");
  return value;
}

export function createSpeechRetrievalClient(options: SpeechRetrievalClientOptions): SpeechRetrievalClient {
  const baseUrl = normalizedBaseUrl(options.baseUrl);
  if (!options.fetch && !globalThis.fetch) throw new TypeError("A fetch implementation is required");

  async function request<T>(
    path: string,
    init: RequestInit = {},
    management = false,
  ): Promise<T> {
    const fetchImplementation = options.fetch ?? globalThis.fetch;
    const headers = new Headers(init.headers);
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    if (management && options.operatorToken) headers.set("Authorization", `Bearer ${options.operatorToken}`);
    const response = await fetchImplementation(`${baseUrl}${path}`, { ...init, headers });
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Preserve a useful HTTP error when a proxy returns a non-JSON body.
    }
    if (!response.ok) throw new SpeechRetrievalApiError(response, (body ?? {}) as Partial<ApiErrorBody>);
    return body as T;
  }

  const query = (values: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined) params.set(key, String(value));
    }
    return params.toString();
  };
  const part = (value: string) => encodeURIComponent(value);

  return {
    search({ query: searchQuery, language, matchMode = "auto", order = "ranked", limit = 20, seed, signal }) {
      const params = query({ q: searchQuery, language, match_mode: matchMode, order, limit, seed });
      return request<SearchResponse>(`/search?${params}`, { signal });
    },
    async suggestions({ language, limit = 12, signal }) {
      const body = await request<SuggestionsResponse>(
        `/suggestions?${query({ language, limit })}`,
        { signal },
      );
      return body.suggestions;
    },
    clip(segmentId, requestOptions) {
      return request<SpeechClip>(`/clips/${part(segmentId)}`, { signal: requestOptions?.signal });
    },
    requestTranslation(segmentId, { targetLanguage, signal }) {
      return request<TranslationJob>(`/clips/${part(segmentId)}/translations`, {
        method: "POST", body: JSON.stringify({ target_language: targetLanguage }), signal,
      });
    },
    translation(jobId, requestOptions) {
      return request<TranslationJob>(`/translations/${part(jobId)}`, { signal: requestOptions?.signal });
    },
    cancelTranslation(jobId, requestOptions) {
      return request<TranslationJob>(`/translations/${part(jobId)}`, {
        method: "DELETE", signal: requestOptions?.signal,
      });
    },
    createTranslationBatch({ segmentIds, targetLanguage, signal }) {
      return request<TranslationBatch>("/translation-batches", {
        method: "POST", body: JSON.stringify({ segment_ids: segmentIds, target_language: targetLanguage }), signal,
      });
    },
    translationBatch(batchId, requestOptions) {
      return request<TranslationBatch>(`/translation-batches/${part(batchId)}`, { signal: requestOptions?.signal });
    },
    cancelTranslationBatch(batchId, requestOptions) {
      return request<TranslationBatch>(`/translation-batches/${part(batchId)}`, {
        method: "DELETE", signal: requestOptions?.signal,
      });
    },
    status(requestOptions) {
      return request<CorpusStatus>("/status", { signal: requestOptions?.signal });
    },
    statistics(requestOptions) {
      return request<CorpusStatistics>("/statistics", { signal: requestOptions?.signal });
    },
    live(requestOptions) {
      return request<LiveHealth>("/health/live", { signal: requestOptions?.signal });
    },
    ready(requestOptions) {
      return request<ReadyHealth>("/health/ready", { signal: requestOptions?.signal });
    },
    channels(language, requestOptions) {
      const suffix = language === undefined ? "" : `?${query({ language })}`;
      return request<ChannelRecord[]>(`/channels${suffix}`, { signal: requestOptions?.signal });
    },
    addChannel(channel, requestOptions) {
      return request<ChannelRecord>("/channels", {
        method: "POST",
        body: JSON.stringify(channel),
        signal: requestOptions?.signal,
      }, true);
    },
    updateChannel(language, channelId, update, requestOptions) {
      return request<ChannelRecord>(`/channels/${part(language)}/${part(channelId)}`, {
        method: "PATCH",
        body: JSON.stringify(update),
        signal: requestOptions?.signal,
      }, true);
    },
    setChannelEnabled(language, channelId, enabled, requestOptions) {
      return request<ChannelRecord>(
        `/channels/${part(language)}/${part(channelId)}/${enabled ? "enable" : "disable"}`,
        { method: "POST", signal: requestOptions?.signal },
        true,
      );
    },
  };
}
