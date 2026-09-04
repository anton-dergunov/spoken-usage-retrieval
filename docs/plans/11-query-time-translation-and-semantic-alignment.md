# Plan 11: Query-time translation and semantic alignment

**Status:** Planned

**Depends on:** Plans 05, 08, and 10

## Outcome

Enrich an opened clip with a natural translation into any requested BCP-47 target language plus
source-to-target character alignment groups. Source playback is immediate, no provider is required,
and each uncached source/target/model/prompt combination uses exactly one generation call.

## Current state

- The prototype shows source text only.
- A completed English-track probe found three directly downloadable authored English tracks among
  ten cached videos; advertised automatic translations repeatedly failed with HTTP 429.
- The earlier plan assumes English and permits YouTube auto-translation. The multilingual product
  requires a caller-selected target language and a more dependable optional path.

## Decisions

- Translation happens only for a selected clip. It never changes indexing, retrieval, ranking, or
  the canonical source transcript.
- Make one schema-constrained LLM request on a cache miss. It returns natural target text and
  semantic groups referencing source and target character ranges; schema repair must not make a
  hidden second generation call.
- Define a small injected provider protocol. Add optional Gemini and OpenAI-compatible adapters
  when credentials are configured; retain a fake provider for deterministic tests.
- With no provider/key, return the source immediately and expose an authored target-language track
  statically when available. Never depend on YouTube-generated translations.
- Use a simple in-process asynchronous job registry backed by the cache/database already in use.
  Do not add a queue service solely for interactive translation.
- Treat semantic alignment as display guidance, not literal word equivalence. Groups may be
  many-to-one, one-to-many, reordered, or explicitly unaligned.

## Implementation work

1. Define a versioned provider-neutral input/output schema with source/target languages, translation,
   complete character-range groups, and optional warnings.
2. Validate range bounds and target coverage, preserve whitespace/punctuation, and store invalid
   provider output as a diagnosable failure rather than attempting unbounded repair.
3. Implement the provider protocol and optional adapters with explicit timeouts and cancellation.
   Keep SDK imports out of the no-provider path.
4. Cache by source-text hash, source language, target language, provider/model, prompt version, and
   output-schema version. Coalesce concurrent identical requests.
5. Add clip translation request, status, result, and cancellation operations. Cancellation is best
   effort and must never cancel source playback or discard a previously completed cache entry.
6. Map a matching creator-authored track into a static fallback/reference response with its own
   provenance. Defer cross-track semantic alignment unless an experiment demonstrates its value.
7. Record latency, cache hits, provider/model/prompt provenance, validation failures, cancellation,
   and approximate cost when the provider supplies usage data.

## Public interfaces and data

- `POST /api/v1/clips/{segment_id}/translations` accepts `target_language`; status/result and
  cancellation routes use a stable job ID. A cache hit may complete immediately.
- States are `not_requested`, `queued`, `running`, `complete`, `failed`, `cancelled`, and
  `unavailable` with a small stable error vocabulary.
- A completed result contains source/target languages, source text hash, target text, semantic
  groups, provenance (`llm` or `authored_track`), and version metadata.
- Translation language is never hard-coded to English and never appears in an index key.

## Acceptance tests and verification

- Schema tests cover reordered, overlapping semantic, one-to-many, untranslated, punctuation, and
  non-Latin examples while rejecting out-of-bounds or uncovered target output.
- An instrumented fake proves exactly one provider generation call per cache miss and zero on a
  cache hit or coalesced duplicate request.
- No-provider tests show source immediately, use an authored matching track when present, and return
  a clear unavailable state otherwise.
- Status polling and cancellation do not block clip lookup/playback; late provider completion is
  handled consistently.
- Optional live tests for each configured adapter are manually invoked, cost-bounded, and excluded
  from ordinary CI.

## Non-goals

- Translating the corpus in advance, translating search queries, or cross-language retrieval.
- Requiring an LLM in production or treating an authored target track as perfect semantic alignment.
- Streaming tokens, a provider marketplace, automatic prompt optimization, or a durable distributed
  translation queue.
