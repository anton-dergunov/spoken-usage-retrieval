import type { CorpusStatus, SearchResponse, Suggestion } from "./types";

async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body as T;
}

export async function searchCorpus(query: string, signal?: AbortSignal): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, limit: "20" });
  return responseJson<SearchResponse>(await fetch(`/api/search?${params}`, { signal }));
}

export async function getSuggestions(): Promise<Suggestion[]> {
  const body = await responseJson<{ suggestions: Suggestion[] }>(
    await fetch("/api/suggestions?limit=12")
  );
  return body.suggestions;
}

export async function getStatus(): Promise<CorpusStatus> {
  return responseJson<CorpusStatus>(await fetch("/api/status"));
}

