# Plan 08: Target-language text

**Status:** Planned

**Depends on:** Plan 07

## Outcome

Give an opened clip readable text in whatever language the caller asks for: a creator-authored track
when one exists, and otherwise one literal-leaning LLM translation with source-to-target character
alignment groups. Source playback is immediate, no provider is required, and each uncached
source/target/model/prompt combination uses exactly one generation call.

This plan merges the authored-track acquisition work with the query-time translation work, because
both exist to answer the same question — what does this clip say in my language — and the authored
track is the no-provider fallback for the translation path.

## Current state

- Acquisition downloads one caption track per video in the source language.
- Track metadata is not rich enough to distinguish authored captions, source-language automatic
  captions, creator translations, and generated translation offers throughout the pipeline.
- The prototype shows source text only.
- A completed English-track probe found three directly downloadable authored English tracks among
  ten cached videos, while advertised automatic translations repeatedly failed with HTTP 429.
- A completed literalness probe found authored English tracks fluent but not literal enough to be the
  learner-facing authority on their own.

## Decisions

### Acquisition of authored tracks

- Select the canonical source track using the catalogue source language: prefer creator-authored
  captions, then original-language automatic captions. Record the chosen rule and the actual result.
- Preserve each downloadable creator-authored subtitle track, regardless of target language, when it
  is returned directly by the provider. Exclude generated auto-translations from routine acquisition;
  they are neither reliable nor authored.
- Keep one source transcript as the only indexing input. Secondary authored tracks are optional
  reference and translation material and never replace source text implicitly.
- Keep the implementation provider-specific and small. Do not build a general subtitle-policy engine
  until another provider or a concrete exception requires one.

### Translation

- Translation happens only for a selected clip. It never changes indexing, retrieval, ranking, or
  the canonical source transcript.
- Target language is a request parameter, never a build-time constant, and never appears in an index
  key. English has no special status.
- Make one schema-constrained LLM request on a cache miss. It returns target text and semantic groups
  referencing source and target character ranges; schema repair must not make a hidden second
  generation call.
- Ask for a **literal, learner-facing** rendering rather than an idiomatic one, because the reader is
  looking at the source text beside it and wants to see which source words carry which meaning.
- When an authored target-language track covers the same time range, pass it into the request as a
  reference to be corrected toward literalness rather than trusted verbatim. The probe showed
  authored tracks are a good prior and a poor authority.
- Define a small injected provider protocol. Add optional adapters when credentials are configured;
  retain a fake provider for deterministic tests. Keep provider SDK imports out of the no-provider
  path.
- With no provider or key, return the source immediately and expose a matching authored
  target-language track statically when one exists. Never depend on YouTube-generated translations.
- Use a simple in-process asynchronous job registry backed by the cache/database already in use. Do
  not add a queue service solely for interactive translation.
- Treat semantic alignment as display guidance, not literal word equivalence. Groups may be
  many-to-one, one-to-many, reordered, or explicitly unaligned.

## Implementation work

1. Enumerate available tracks once per video and normalize their language, display name, provider
   track ID, authored/automatic kind, translatability flag, and source URL metadata. Choose and
   download the canonical source track using a deterministic preference order and the existing
   retry/cache behavior.
2. Download directly available creator-authored secondary tracks independently, store each in a
   distinct cache entry, and add a compact manifest linking the canonical source and optional
   secondary tracks. Record individual failures without failing or deleting a valid source
   transcript.
3. Carry caption provenance into transcript metadata, segments, reports, API results, and the demo's
   diagnostic display, and extend acquisition reporting with source-track selection and
   authored-secondary coverage counts.
4. Define a versioned provider-neutral translation schema with source/target languages, translation,
   complete character-range groups, and optional warnings. Validate range bounds and target coverage,
   preserve whitespace and punctuation, and store invalid provider output as a diagnosable failure
   rather than attempting unbounded repair.
5. Implement the provider protocol and optional adapters with explicit timeouts and cancellation, and
   a prompt that requests literal rendering and accepts an optional authored-track reference.
6. Cache by source-text hash, source language, target language, provider/model, prompt version, and
   output-schema version. Coalesce concurrent identical requests.
7. Add clip translation request, status, result, and cancellation operations. Cancellation is best
   effort and must never cancel source playback or discard a completed cache entry.
8. Map a matching creator-authored track into a static fallback response with its own provenance.
   Defer cross-track semantic alignment unless an experiment demonstrates its value.
9. Add the translation states to `SpeechClipPlayer` through the props Plan 06 reserved, and record
   latency, cache hits, provenance, validation failures, cancellation, and approximate cost when the
   provider supplies usage data.

## Public interfaces and data

- A caption track records `track_id`, `language`, `kind` (`authored` or `automatic`), `is_source`,
  provider provenance, acquisition time, and content checksum. A video manifest names exactly one
  canonical source track when acquisition succeeds and zero or more secondary authored tracks.
- Source-language acquisition success is independent from secondary-track status.
- `POST /api/v1/clips/{segment_id}/translations` accepts `target_language`; status/result and
  cancellation routes use a stable job ID. A cache hit may complete immediately.
- States are `not_requested`, `queued`, `running`, `complete`, `failed`, `cancelled`, and
  `unavailable` with a small stable error vocabulary.
- A completed result contains source/target languages, source text hash, target text, semantic
  groups, provenance (`llm` or `authored_track`), and version metadata.

## Acceptance tests and verification

- Fixtures cover authored-source preference, automatic-source fallback, multiple authored languages,
  generated translation exclusion, and ambiguous/missing language metadata.
- A secondary download failure still produces a valid source manifest and indexable transcript, and
  repeat acquisition safely fills previously missing optional tracks.
- Reports distinguish authored source, automatic source, authored secondary, unavailable, and failed
  tracks without counting a generated translation as authored coverage.
- Schema tests cover reordered, one-to-many, untranslated, punctuation-heavy, and non-Latin examples
  while rejecting out-of-bounds or uncovered target output.
- An instrumented fake proves exactly one provider generation call per cache miss and zero on a cache
  hit or coalesced duplicate request, including when an authored reference is supplied.
- No-provider tests show source immediately, use an authored matching track when present, and return
  a clear unavailable state otherwise.
- Status polling and cancellation do not block clip lookup or playback; late provider completion is
  handled consistently.
- Optional live tests for each configured adapter are manually invoked, cost-bounded, and excluded
  from ordinary CI.
- Existing source-only acquisition and search tests continue to pass.

## Non-goals

- Indexing translated text, translating the corpus in advance, translating search queries, or
  cross-language retrieval.
- Downloading every generated YouTube translation, synchronizing tracks, or judging translation
  quality automatically.
- Requiring an LLM in production or treating an authored target track as perfect semantic alignment.
- Streaming tokens, a provider marketplace, automatic prompt optimization, or a durable distributed
  translation queue.
- Supporting arbitrary video providers before one is actually added.
