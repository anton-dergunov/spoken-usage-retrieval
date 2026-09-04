import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

const suggestionResponse = {
  suggestions: [{ text: "la verdad", normalized: "la verdad", size: 2, occurrences: 4, videos: 2 }]
};
const statusResponse = {
  ready: true, version: "0.1.0", built_at: "2026-09-04T00:00:00Z", max_ngram: 5,
  videos: 10, segments: 321, occurrences: 4000, caption_kinds: { manual: 2, automatic: 8 }
};
const searchResponse = {
  query: "la verdad", normalized_query: "la verdad", total_occurrences: 2, returned: 2,
  results: [
    {
      occurrence_id: "one", sentence: "La verdad es una buena idea.",
      match: { text: "La verdad", char_start: 0, char_end: 9, accent_exact: true },
      sentence_start: 1, sentence_end: 3, clip_start: .65, clip_end: 3.65,
      boundary: { reason: "punctuation", confidence: 1 }, quality_score: .93,
      video: { provider: "youtube", id: "one", url: "https://youtube.test/one", title: "One",
        channel_id: "c1", channel: "Easy Spanish", varieties: ["Mexico"], speech_style: ["conversation"],
        duration: 100, thumbnail: null, caption_kind: "manual", caption_language: "es" }
    },
    {
      occurrence_id: "two", sentence: "Esa es, la verdad, otra historia.",
      match: { text: "la verdad", char_start: 8, char_end: 17, accent_exact: true },
      sentence_start: 5, sentence_end: 8, clip_start: 4.65, clip_end: 8.65,
      boundary: { reason: "punctuation", confidence: 1 }, quality_score: .87,
      video: { provider: "youtube", id: "two", url: "https://youtube.test/two", title: "Two",
        channel_id: "c2", channel: "LUZU TV", varieties: ["Argentina"], speech_style: ["conversation"],
        duration: 100, thumbnail: null, caption_kind: "automatic", caption_language: "es-orig" }
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
});

it("renders API failures as an accessible notice", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
    JSON.stringify({ detail: "Search index not found" }),
    { status: 503, headers: { "Content-Type": "application/json" } }
  ))));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Search index not found");
});
