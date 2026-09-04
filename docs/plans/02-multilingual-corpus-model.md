# Plan 02: Multilingual corpus model

**Status:** Planned

**Depends on:** Plan 01

## Outcome

Remove Spanish as an architectural assumption. One installation can index and query several source
languages, while every original string remains unchanged and translations stay separate derived
data requested in a target language later.

## Current state

- Caption selection, stopwords, error messages, UI copy, metadata defaults, and package description
  are Spanish-specific.
- A broad descriptive Spanish catalogue and the executable four-channel MVP subset currently live
  in separate files with overlapping channel records.
- The database does not store a source language on videos, segments, occurrences, or statistics.
- `Corpus.search` has no language argument, so identical normalized terms across languages would
  collide.

## Decisions

- Use canonical BCP-47 tags such as `es`, `pt-BR`, and `zh-Hans`. The catalogue-level `language` is
  the default source language; every persisted transcript and derived row repeats it explicitly.
- Keep `varieties` as curator-facing descriptive strings because labels such as “Rioplatense” and
  “Latin America” do not map cleanly to a locale. Do not use them as language identity.
- Store all languages in one SQLite corpus with language-leading indexes. A stable segment identity
  includes provider video ID, source language, track identity, timing, and source-text hash.
- Merge catalogue and activation state: each channel has `id`, `name`, `url`, `enabled`,
  `varieties`, `speech_style`, and `description` in `config/channels/<language>.json`.
- An enabled/disabled change controls future discovery only. Existing indexed material remains
  searchable until an explicit purge introduced later.

## Implementation work

1. Define a versioned channel-catalogue schema. Validate the fields needed to run safely—stable ID,
   name, URL, source language, and enabled state—with actionable field paths. Preserve optional
   editorial descriptions, styles, and varieties without requiring artificial placeholder values.
2. Merge the two Spanish sources into `config/channels/es.json`, preserving the broad catalogue and
   marking the current four MVP channels enabled. Remove the superseded files and update commands,
   tests, and documentation atomically.
3. Add source language and catalogue identity to acquisition metadata. Partition raw and derived
   cache manifests by stable corpus/video/track IDs so two language tracks cannot overwrite each
   other.
4. Evolve SQLite with explicit schema versioning and language columns on videos/transcripts,
   segments, occurrences, and n-gram statistics. Rebuild disposable pre-version indexes rather than
   maintaining compatibility readers.
5. Scope every lookup, count, suggestion, status aggregation, and diversity operation by source
   language. Remove the global Spanish stopword constant; analyzers provide language resources and
   the fallback simply suppresses no language-specific words.
6. Make the demo load available languages from status/configuration, select a source language, and
   use language-neutral empty states and labels. Spanish remains the default only when it is the
   sole enabled corpus.
7. Record source language, catalogue schema version, and analyzer identity in build/acquisition
   reports so experiments can be reproduced.

## Public interfaces and data

- Add required `source_language` to Python search and suggestion operations and `language` to their
  HTTP query parameters.
- Responses add `source_language`; video caption metadata distinguishes track language from corpus
  source language.
- Status returns configured/enabled/indexed languages and per-language counts.
- Cache/database versions record enough information to request a reindex when stored data is
  genuinely incompatible; avoid compatibility machinery for disposable prototype indexes.

## Acceptance tests and verification

- Unit tests cover common valid/invalid BCP-47 tags, duplicate channels, activation, optional
  editorial fields, and catalogue round trips without silently discarding curator content.
- A synthetic Spanish/English corpus containing the same surface token returns only the requested
  language in search, suggestions, counts, and status.
- Rebuilding from the migrated Spanish catalogue reproduces the existing MVP search behavior.
- UI tests select a source language and verify that requests and result metadata preserve it.
- Repository search finds no Spanish language default in acquisition, indexing, search, API, or
  reusable UI code; Spanish may remain only in fixtures, catalogue content, and demo examples.

## Non-goals

- Curating new language catalogues; Plan 03 owns that content.
- Language-aware tokenization or lemmatization; Plan 04 owns analysis.
- Translations, translated-track acquisition, or cross-language retrieval.
- Migrating irreplaceable user data; generated prototype indexes are rebuilt.
