import type { CorpusStatus, SearchResponse, Suggestion } from "./types";

async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body as T;
}

export async function searchCorpus(
  query: string,
  language: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, language, limit: "20" });
  return responseJson<SearchResponse>(await fetch(`/api/search?${params}`, { signal }));
}

export async function getSuggestions(
  language: string,
  signal?: AbortSignal,
): Promise<Suggestion[]> {
  const params = new URLSearchParams({ language, limit: "12" });
  const body = await responseJson<{ source_language: string; suggestions: Suggestion[] }>(
    await fetch(`/api/suggestions?${params}`, { signal })
  );
  return body.suggestions;
}

export async function getStatus(): Promise<CorpusStatus> {
  return responseJson<CorpusStatus>(await fetch("/api/status"));
}
