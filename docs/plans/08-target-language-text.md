# Plan 08: Target-language text and word alignment

**Status:** Complete

**Depends on:** Plans 02, 05, and 06. Plan 07 and Acervo are not required.

## Outcome

An opened clip can display a caller-selected target language without changing acquisition, indexing,
retrieval, ranking, or immediate source playback. A matching creator-authored caption is the
no-provider fallback. Otherwise the service performs two independently cached Gemini operations:

1. translate the complete source sentence;
2. align fixed, occurrence-labeled lexical tokens in the immutable source and target strings.

The normalized alignment is a many-to-many bipartite graph. Character offsets are derived locally;
caption cues or future forced-alignment timing only project the graph onto playback. A valid
translation remains visible when alignment is invalid or unavailable.

## Acquisition and fallback

- Enumerate a video's caption tracks once and record provider track ID, normalized BCP-47 language,
  authored/automatic kind, canonical-source choice, acquisition status, checksum, and provenance.
- Select creator-authored source captions first, then same-language automatic captions. Index only
  the canonical source track.
- Download directly available authored secondary tracks independently and exclude generated
  translation offers. A secondary failure never invalidates source acquisition and is retried
  without redownloading valid tracks.
- For fallback, combine authored target cues overlapping the source sentence interval. Prefer an
  exact language match, then the same primary language, and return actual track provenance without
  semantic alignment.

## Translation and word-alignment stages

- `TranslationProvider.translate` receives exact text, source/target tags, and an optional authored
  excerpt. The versioned packaged prompt requests one literal-leaning, grammatical, learner-facing
  `target_text` at temperature 0.2.
- `WordAlignmentProvider.align` receives immutable strings and lexical nodes labeled `S1…Sn` and
  `T1…Tn`. At temperature 0.0 it returns one adjacency row per source node plus explicit unaligned
  target IDs.
- Request-specific JSON Schema enums constrain all IDs. Local validation additionally enforces
  source row order/completeness, unique known IDs and edges, exclusive linked/unaligned accounting,
  nonempty graphs, and Unicode bounds. There is no hidden repair call.
- Repeated forms are distinct nodes. One-to-many, many-to-one, crossing, and noncontiguous edges are
  legal. One compatibility `SemanticAlignmentGroup` is derived per linked source token.
- Source tokens use stored analysis, deduplicating Stanza multi-word expansions sharing a span.
  Target tokens use the configured analyzer infrastructure and record its identity. Whitespace-free
  CJK with only unreliable fallback segmentation returns translation with alignment unavailable.

## Persistent derived cache and jobs

- `data/derived/translations.sqlite3` is independent of the rebuildable corpus index.
- Translation keys include source hash/languages, provider/model, prompt version/hash, schema, and
  authored-reference checksum.
- Alignment keys include source/target hashes, both token sequences and tokenizer identities,
  languages, provider/model, and alignment prompt version/hash/schema. Timing is intentionally not
  a key input.
- Translation and alignment successes and schema-invalid terminal failures are stored separately.
  Temporary transport/rate failures remain retryable. Append-only attempts retain stage, raw output,
  internal validation failure, latency, usage, and provider metadata for diagnosis.
- One in-process scheduler bounds provider calls (default concurrency four), coalesces identical
  work, preserves per-request job IDs, isolates cancellation, and marks unfinished jobs interrupted
  on restart.
- `retry_failed: true` is accepted only as an explicit caller action. It reuses successful translation
  and makes exactly one alignment call when only alignment failed. A successful cache entry is never
  bypassed.
- Batches atomically validate up to 50 unique segment IDs and use the identical scheduler/cache path.
  Reindexing never deletes translation or alignment entries. The operator CLI lists statistics and
  prunes deliberately by language, provider/model, or age.
- The obsolete joint experimental cache schema is invalidated during the schema-2 migration; no
  production compatibility implementation for its free-form chunks is retained.

## Public interfaces

- `POST /api/v1/clips/{segment_id}/translations`
- `GET`/`DELETE /api/v1/translations/{job_id}`
- `POST /api/v1/translation-batches`
- `GET`/`DELETE /api/v1/translation-batches/{batch_id}`

Requests accept any valid target BCP-47 tag and reject the canonical source language. Advertised
target languages configure only the standalone demo. Job states remain `not_requested`, `queued`,
`running`, `complete`, `failed`, `cancelled`, `interrupted`, and `unavailable`. Results independently
report alignment `complete`, `failed`, or `unavailable` with stable public error codes and no raw
provider/validator message.

Python, OpenAPI, and TypeScript export `AlignmentToken`, `WordAlignmentEdge`,
`WordAlignmentGraph`, the derived character groups, stage provenance/status, provider protocols,
jobs, batches, and cache statistics.

## Player behavior

- The demo requests translation when a clip/language becomes active and cancels obsolete polling.
  `retry_failed` is sent only after an explicit retry click.
- Source or target token hover/focus highlights direct graph neighbors. Click/tap pins a relation;
  a second tap, background tap, or Escape clears it. A pin takes precedence over playback.
- Playback finds source nodes intersecting the current timing range and highlights the union of their
  target neighbors. Finer future source timing automatically improves projection without a new
  semantic alignment.
- With fewer than two distinct source timing units or no internal boundary, automatic target
  highlighting is disabled because highlighting the whole translation conveys no information.
- Authored fallback stays static. Queued, failed, cancelled, and unavailable translation never
  blocks source playback. Learner UI shows neutral retry controls and never raw backend errors.

## Research evidence

The [fixed-token word-alignment experiment](../../experiments/target-language-word-alignment/README.md)
contains the 50-pair Spanish/German/Russian/Chinese/Japanese challenge set, exact call chronology,
prompt/schema comparison, AER/F1/coverage/latency/usage metrics, structured successes and failures,
translation chrF++ plus manual review, limitations, licenses, and a dated bibliography covering
SimAlign, AWESOME-align, cross-language span prediction, Lexi-align, XL-WA, Azure alignment, and
Gemini structured output.

The locked held-out run produced 39/40 structurally valid graphs and 0.879 end-to-end micro F1 when
the invalid graph is scored empty. The complete pipeline produced 50/50 valid translations and
49/50 valid alignments. The former free-chunk study is retained only as a superseded historical
baseline; its runtime heuristics are not part of this implementation.

## Non-goals

- Indexing translated text, translating the whole corpus, translating search queries, or
  cross-language retrieval.
- Downloading generated YouTube translations or semantically aligning authored fallback captions.
- Adding PyTorch/model weights for SimAlign/AWESOME-align before an operational need justifies them.
- Training a supervised span aligner without professionally annotated in-domain data.
- A provider marketplace, streaming generation, or a durable distributed queue.
