import type { CorpusStatus, MatchMode, SearchResponse, Suggestion } from "./types";

async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    const detail = body.error?.message;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return body as T;
}

export async function searchCorpus(
  query: string,
  language: string,
  signal?: AbortSignal,
  matchMode: MatchMode = "auto",
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, language, match_mode: matchMode, limit: "20" });
  return responseJson<SearchResponse>(await fetch(`/api/v1/search?${params}`, { signal }));
}

export async function getSuggestions(
  language: string,
  signal?: AbortSignal,
): Promise<Suggestion[]> {
  const params = new URLSearchParams({ language, limit: "12" });
  const body = await responseJson<{ source_language: string; suggestions: Suggestion[] }>(
    await fetch(`/api/v1/suggestions?${params}`, { signal })
  );
  return body.suggestions;
}

export async function getStatus(): Promise<CorpusStatus> {
  return responseJson<CorpusStatus>(await fetch("/api/v1/status"));
}
