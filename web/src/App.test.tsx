import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

const suggestionResponse = {
  source_language: "es",
  suggestions: [{ source_language: "es", text: "la verdad", normalized: "la verdad", size: 2, occurrences: 4, videos: 2 }]
};
const statusResponse = {
  ready: true, package_version: "0.1.0", database_schema_version: 1,
  built_at: "2026-09-04T00:00:00Z", max_ngram: 5, analyzer_id: "unicode-regex-v1",
  configured_languages: ["es"], enabled_languages: ["es"], indexed_languages: ["es"],
  languages: [{ source_language: "es", configured: true, enabled: true, indexed: true,
    configured_channels: 24, enabled_channels: 4, videos: 10, segments: 321, occurrences: 4000,
    caption_kinds: { manual: 2, automatic: 8 }, analyzer_id: "unicode-regex-v1" }],
  videos: 10, segments: 321, occurrences: 4000, caption_kinds: { manual: 2, automatic: 8 }
};
const searchResponse = {
  query: "la verdad", normalized_query: "la verdad", source_language: "es",
  total_occurrences: 2, returned: 2,
  match_mode: "auto", morphology_available: true, morphology_unavailable_reason: null,
  totals_by_mode: { exact: 1, lemma: 2, auto: 2 }, query_analyses: [],
  results: [
    {
      match_type: "exact",
      occurrence_id: "one", segment_id: "segment-one", source_language: "es",
      sentence: "La verdad es una buena idea.",
      match: { text: "La verdad", char_start: 0, char_end: 9, accent_exact: true },
      sentence_start: 1, sentence_end: 3, clip_start: .65, clip_end: 3.65,
      segments: [{ text: "La verdad es una buena idea.", start: 1, end: 3, char_start: 0, char_end: 29 }],
      boundary: { reason: "punctuation", confidence: 1 }, quality_score: .93,
      video: { video_key: "video-one", provider: "youtube", id: "one", url: "https://youtube.test/one", title: "One",
        channel_id: "c1", channel: "Easy Spanish", source_language: "es", varieties: ["Mexico"], speech_style: ["conversation"],
        duration: 100, thumbnail: null, track_id: "track-one", caption_kind: "manual", caption_language: "es" }
    },
    {
      match_type: "lemma",
      occurrence_id: "two", segment_id: "segment-two", source_language: "es",
      sentence: "Esa es, la verdad, otra historia.",
      match: { text: "la verdad", char_start: 8, char_end: 17, accent_exact: true },
      sentence_start: 5, sentence_end: 8, clip_start: 4.65, clip_end: 8.65,
      segments: [{ text: "Esa es, la verdad, otra historia.", start: 5, end: 8, char_start: 0, char_end: 33 }],
      boundary: { reason: "punctuation", confidence: 1 }, quality_score: .87,
      video: { video_key: "video-two", provider: "youtube", id: "two", url: "https://youtube.test/two", title: "Two",
        channel_id: "c2", channel: "LUZU TV", source_language: "es", varieties: ["Argentina"], speech_style: ["conversation"],
        duration: 100, thumbnail: null, track_id: "track-two", caption_kind: "automatic", caption_language: "es-orig" }
    }
  ]
};

afterEach(() => vi.restoreAllMocks());

it("offers corpus suggestions and loads diverse results", async () => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("suggestions") ? suggestionResponse : url.includes("status") ? statusResponse : searchResponse;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
  render(<App />);
  expect(await screen.findByText("10 videos")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "la verdad" }));
  expect(await screen.findByText("Results for “la verdad”")).toBeInTheDocument();
  expect(screen.getByText("Easy Spanish", { selector: ".result-meta span" })).toBeInTheDocument();
  expect(screen.getByText("LUZU TV", { selector: ".result-meta span" })).toBeInTheDocument();
  expect(screen.getAllByText("La verdad").length).toBeGreaterThan(0);
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("language=es"))).toBe(true);
});

it("requires a language choice when several enabled corpora are indexed", async () => {
  const multilingualStatus = {
    ...statusResponse,
    configured_languages: ["en", "es"], enabled_languages: ["en", "es"],
    indexed_languages: ["en", "es"],
  };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const body = String(input).includes("status") ? multilingualStatus : {
      source_language: "en", suggestions: [],
    };
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  }));
  render(<App />);
  const input = await screen.findByLabelText("Search source-language speech");
  expect(input).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Source language"), { target: { value: "en" } });
  expect(input).toBeEnabled();
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(
    ([request]) => String(request).includes("suggestions") && String(request).includes("language=en")
  )).toBe(true));
});

it("renders API failures as an accessible notice", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
    JSON.stringify({ detail: "Search index not found" }),
    { status: 503, headers: { "Content-Type": "application/json" } }
  ))));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Search index not found");
});

it("uses the system color scheme initially and allows a session-only override", async () => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("suggestions") ? suggestionResponse : statusResponse;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  }));
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));

  render(<App />);
  const colorSwitch = screen.getByRole("switch", { name: "Use dark color scheme" });
  expect(colorSwitch).toHaveAttribute("aria-checked", "true");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");

  fireEvent.click(colorSwitch);
  expect(colorSwitch).toHaveAttribute("aria-checked", "false");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  expect(localStorage).toHaveLength(0);
});


it("defaults to all forms and lets users compare exact and lemma searches", async () => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("suggestions") ? suggestionResponse : url.includes("status") ? statusResponse : searchResponse;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }));
  render(<App />);
  expect(screen.getByLabelText("Word forms")).toHaveValue("auto");
  fireEvent.click(await screen.findByRole("button", { name: "la verdad" }));
  await screen.findByText("Results for “la verdad”");
  expect(screen.getByText("Related form")).toBeInTheDocument();
  expect(screen.getByText("Exact form", { selector: ".match-type" })).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("match_mode=auto"))).toBe(true);
  for (const mode of ["exact", "lemma"]) {
    fireEvent.change(screen.getByLabelText("Word forms"), { target: { value: mode } });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes(`match_mode=${mode}`))).toBe(true));
  }
});

it("explains exact-only fallback and renders typed unsupported-analysis errors", async () => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const unsupported = url.includes("match_mode=lemma");
    const body = url.includes("status") ? statusResponse : url.includes("suggestions") ? suggestionResponse : unsupported
      ? { detail: { code: "unsupported_analysis", message: "No local morphology model" } }
      : { ...searchResponse, morphology_available: false };
    return Promise.resolve(new Response(JSON.stringify(body), { status: unsupported ? 400 : 200 }));
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "la verdad" }));
  expect(await screen.findByText("Only exact forms are available for this language in the current index.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Word forms"), { target: { value: "lemma" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("No local morphology model");
});

it("aborts the previous search when the word-form mode changes", async () => {
  let oldSignal: AbortSignal | undefined;
  let finishOld: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("match_mode=auto")) {
      oldSignal = init?.signal ?? undefined;
      return new Promise<Response>((resolve) => { finishOld = resolve; });
    }
    const body = url.includes("status") ? statusResponse : url.includes("suggestions") ? suggestionResponse : { ...searchResponse, query: "new mode" };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "la verdad" }));
  await waitFor(() => expect(oldSignal).toBeDefined());
  fireEvent.change(screen.getByLabelText("Word forms"), { target: { value: "exact" } });
  expect(oldSignal?.aborted).toBe(true);
  await screen.findByText("Results for “new mode”");
  finishOld?.(new Response(JSON.stringify(searchResponse), { status: 200 }));
  await waitFor(() => expect(screen.queryByText("Results for “la verdad”")).not.toBeInTheDocument());
});
