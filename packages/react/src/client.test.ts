import { describe, expect, it, vi } from "vitest";
import { createSpeechRetrievalClient, SpeechRetrievalApiError } from "./client.js";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" },
  });
}

describe("createSpeechRetrievalClient", () => {
  it("uses the supplied base URL and fetch implementation for read routes", async () => {
    const fetchImplementation = vi.fn(() => Promise.resolve(json({ results: [] })));
    const client = createSpeechRetrievalClient({
      baseUrl: "https://speech.test/api/v1/",
      fetch: fetchImplementation,
      operatorToken: "secret",
    });
    await client.search({ query: "la verdad", language: "es", matchMode: "lemma", order: "random", seed: 7, limit: 4 });
    const [url, init] = fetchImplementation.mock.calls[0];
    expect(url).toBe("https://speech.test/api/v1/search?q=la+verdad&language=es&match_mode=lemma&order=random&limit=4&seed=7");
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("sends the operator token only on management mutations", async () => {
    const fetchImplementation = vi.fn(() => Promise.resolve(json({ id: "channel" })));
    const client = createSpeechRetrievalClient({ baseUrl: "/api/v1", fetch: fetchImplementation, operatorToken: "secret" });
    await client.channels();
    await client.setChannelEnabled("pt-BR", "channel/id", true);
    expect(new Headers(fetchImplementation.mock.calls[0][1].headers).has("Authorization")).toBe(false);
    expect(new Headers(fetchImplementation.mock.calls[1][1].headers).get("Authorization")).toBe("Bearer secret");
    expect(fetchImplementation.mock.calls[1][0]).toBe("/api/v1/channels/pt-BR/channel%2Fid/enable");
  });

  it("throws a typed service error", async () => {
    const fetchImplementation = vi.fn(() => Promise.resolve(json({
      error: { code: "unsupported_analysis", message: "No local model", details: { language: "ja" } },
      request_id: "request-1",
    }, 400)));
    const client = createSpeechRetrievalClient({ baseUrl: "/api/v1", fetch: fetchImplementation });
    await expect(client.status()).rejects.toMatchObject<Partial<SpeechRetrievalApiError>>({
      name: "SpeechRetrievalApiError",
      message: "No local model",
      status: 400,
      code: "unsupported_analysis",
      requestId: "request-1",
    });
  });

  it("starts and inspects translation jobs and cache-warming batches", async () => {
    const fetchImplementation = vi.fn(() => Promise.resolve(json({ status: "queued" })));
    const client = createSpeechRetrievalClient({ baseUrl: "/api/v1", fetch: fetchImplementation });
    await client.requestTranslation("segment/1", { targetLanguage: "ru" });
    await client.translation("job/1");
    await client.cancelTranslation("job/1");
    await client.createTranslationBatch({ segmentIds: ["one", "two"], targetLanguage: "en" });
    expect(fetchImplementation.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
      ["/api/v1/clips/segment%2F1/translations", "POST"],
      ["/api/v1/translations/job%2F1", undefined],
      ["/api/v1/translations/job%2F1", "DELETE"],
      ["/api/v1/translation-batches", "POST"],
    ]);
  });
});
