# Plan 04a: Promote analyzer and compact-index findings

**Status:** Complete

**Depends on:** Plan 03, Plan 04

## Outcome

Promote the two accepted Plan 04 findings into production: prefer a locally provisioned Stanza
pipeline for every language, and replace materialized exact/lemma n-gram occurrences with token
streams whose positions reconstruct contiguous one-to-five-token matches.

## Analyzer selection

- `auto` tries the recorded language's local Stanza model first, then simplemma where supported, then
  the Unicode exact-only fallback.
- Explicit `unicode`, `simplemma`, and `stanza` index builds remain available for deployments with
  different quality, memory, and startup constraints.
- The resolved analyzer identity is stored per language. Query analysis derives that choice from the
  index and cannot silently substitute another analyzer.

## Compact index

- Increment the disposable database schema and rebuild from cached captions.
- Store Unicode surface tokens once. Store a separate analyzer-surface stream only when its
  boundaries differ, and store usable lemmas once at their analyzer positions.
- Preserve missing-lemma position gaps and shared multi-word-token spans.
- Seed exact and lemma lookup by the first candidate token, verify each following position in the
  same stream, and derive occurrence IDs and highlight spans from the segment plus first/last source
  offsets.
- Preserve exact/lemma/auto totals, overlap deduplication, exact-first ranking, language isolation,
  suggestions, stable result fields, and the configured n-gram limit.
- Keep only aggregate one-to-three-token suggestion statistics; do not rematerialize searchable
  occurrence n-grams.

## Compatibility and verification

Schema 2 and earlier indexes produce an actionable rebuild error. Raw caption caches and segment
identities remain stable. Verification covers all three explicit analyzers, Stanza-first automatic
selection, one-to-five-token phrases, missing-lemma gaps, MWT spans, totals, highlights, ranking,
suggestions, HTTP behavior, real locally provisioned CJK models, lint, formatting, typing, smoke,
web tests/build, and Python distribution builds.

## Result

Database schema 3 implements the token-position layout and retains stable source-derived occurrence
IDs. Tests cover exact and lemma phrases at every supported length, configured shorter limits,
missing-lemma gaps, and the individual lemma selected from a shared Stanza MWT span. Automatic
analyzer selection now prefers a compatible local Stanza model and retains the explicit analyzer
choices for smaller deployments.

On the same ten-video Spanish caption checksum, the production-shaped schema reduced the Simplemma
database from 81.46 MiB to 19.10 MiB (76.6%) and the Unicode database from 58.07 MiB to 14.96 MiB
(74.2%). Twenty-repeat Simplemma phrase probes had 1.164–1.967 ms medians across one-to-five-token
exact and lemma queries; result volume remained the larger query-time factor.

Verification passed 72 standard Python tests with three explicit optional-model skips, all 16 Stanza
tests with the local Japanese, Korean, and Chinese models, Ruff lint and formatting, mypy, the
offline smoke command, 17 web tests, the production web build, and Python source/wheel builds.
