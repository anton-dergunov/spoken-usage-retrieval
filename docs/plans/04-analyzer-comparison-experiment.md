# Plan 04: Ten-language morphology and compact-index experiment

**Status:** Complete

**Depends on:** Plan 03

## Outcome

Measure the production Unicode, simplemma, and Stanza analyzers across the repository's ten target languages and determine whether a more compact morphology index can reproduce current retrieval semantics. The experiment informs later implementation plans; it does not change production analyzer selection or the database schema.

## Inputs

- Universal Dependencies 2.18, released May 15, 2026, is pinned by archive URL and SHA-256 in [`experiments/morphological-retrieval-multilingual/config.json`](../../experiments/morphological-retrieval-multilingual/config.json).
- The matrix uses English-EWT, Spanish-AnCora, French-GSD, Japanese-GSDLUW, German-GSD, Korean-GSD, Italian-ISDT, Chinese-GSD, Portuguese-Bosque, and Hindi-HDTB.
- The experiment records every treebank split and license checksum. External data and Stanza weights remain under ignored `data/experiments/` paths.
- The locked environments are simplemma 1.2.0 and Stanza 1.14.0. Unicode is evaluated everywhere, simplemma for `en`, `es`, `fr`, `de`, `it`, `pt`, and `hi`, and Stanza everywhere. Unsupported simplemma cells are explicit `N/A` rows.
- Wiktionary-derived candidates are deferred until shipped analyzers show a material remaining need.

## Measurements

Each production analyzer runs against reconstructed UD raw sentences. The scorer validates canonical offsets and reports source-token boundary precision/recall/F1, syntactic-word expansion for multi-word tokens, and end-to-end lemma coverage and accuracy.

Strict lemma equality uses Unicode NFC and case folding without accent removal. Production-key equivalence is reported separately so accent folding cannot hide a linguistic error. Coverage and accuracy are broken down by UPOS, unseen forms, and ambiguous forms; train and development splits define those categories. Stored error examples contain only form, gold/predicted lemma, and POS fields.

A deterministic retrieval manifest uses seed `20260905`. Per language it selects up to 20 lemmas with at least two observed forms and five test occurrences, plus up to 10 ambiguous forms. The manifest records surface query, intended lemma, selection class, and observed forms. Exact, lemma, and auto probes report counts, deduplicated expansion, candidate-lemma recall, intended-lemma precision, ambiguous-union precision, and representative false positives.

Every analyzer/language measurement runs in an isolated process. It records cold initialization, peak RSS, model or dictionary bytes and checksums, sentences and tokens per second, index build time, and warm-query median/p95 over 20 repetitions.

## Compact-index prototype

Three SQLite layouts use identical analyzed data and queries:

1. dual exact/lemma 1–5-gram keys matching the current design;
2. the same rows with route-specific partial indexes;
3. token positions that store Unicode surfaces and analyzer words/lemmas once, seed lookup by the first token, and verify adjacent positions.

The position layout derives occurrence identity and highlight spans from sentence identity plus first/last source offsets. Shared MWT spans and form-to-multiple-lemma observations are retained. Size and timing are reported only after every generated exact and lemma key reproduces reference occurrence IDs, routes, counts, and character spans. `dbstat` separates tables and indexes, and size is normalized as bytes per gold token.

## Delivery

[`experiments/morphological-retrieval-multilingual/`](../../experiments/morphological-retrieval-multilingual/) contains the versioned configuration, explicit downloader, preflight command, scorer, benchmark runner, compact prototype, JSON result schema, machine-readable results, and generated report.

Preflight lists missing UD splits, licenses, package versions, and Stanza models without reducing coverage. Only the explicit preparation command uses the network. The normal test suite runs a fixture-sized end-to-end experiment and covers CoNLL-U reconstruction, Unicode offsets, MWT alignment, strict versus folded scoring, ambiguity and OOV classification, deterministic query selection, explicit unsupported rows, aggregation, missing lemmas, nullable POS, phrase contiguity, and compact-layout parity.

## Completion rule

Mark this plan complete only when all ten languages have traceable Unicode and Stanza results, all seven supported languages have simplemma results, unsupported cells are explicit, and every compact-layout parity check passes. The report then records per-language analyzer guidance and an index-layout recommendation. Any recommended production change becomes a separate implementation plan.

## Result

The September 5–6, 2026 run completed all 27 applicable analyzer/language rows and recorded three explicit simplemma `N/A` rows. Stanza had the strongest coverage-adjusted strict lemma score for every target language. All partial-index and token-position parity checks passed. Across the matrix, token positions reduced SQLite storage from 873.0 MB to 130.1 MB (85.1%) while increasing median warm lookup from 0.111 ms to 0.149 ms. At experiment completion, the report recommended a separate migration plan and left production behavior unchanged.

The user reviewed and accepted both recommendations on September 6, 2026. Their production
promotion is tracked separately in [Plan 04a](04a-production-morphology-promotion.md), preserving
this document as the experiment and decision record.
