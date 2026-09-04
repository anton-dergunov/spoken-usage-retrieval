# Plan 04: Analyzer comparison experiment

**Status:** Planned

**Depends on:** Plan 03

## Outcome

Decide, from measurements rather than intuition, which analyzer each supported language should use.
Report intrinsic lemmatization accuracy and the retrieval-level effect of lemma matching, including
the false positives it introduces. A documented negative result — "the fast default is good enough
everywhere it applies" — is a valid and useful outcome.

This plan is optional and blocks nothing. It exists because analyzer choice is the first real
modeling decision in the pipeline and deserves evidence.

## Current state

- Plan 03 ships simplemma as the default and Stanza as an optional analyzer behind one protocol,
  chosen by language rather than by measurement.
- [`docs/design.md`](../design.md) already names lemma-mode false positives as the risk to quantify
  before lemmatization changes default retrieval behavior.
- No lemmatization accuracy or lemma-retrieval precision has been measured on this corpus.

## Decisions

- Measure three candidates behind the same `TextAnalyzer` protocol:
  1. simplemma, the current default;
  2. Stanza UD pipelines, POS-aware and multi-word-token aware;
  3. a form-to-lemma lexicon derived from Wiktionary via `kaikki.org` wiktextract dumps (CC-BY-SA).
     The third candidate is cheap to try because the companion Acervo repository already downloads
     these dumps for 46 dictionaries and currently discards their `forms` and `inflection_templates`
     fields, which is exactly the inflection data this task needs, with POS attached.
- Run both an intrinsic and an extrinsic evaluation. Intrinsic accuracy is comparable to published
  numbers; extrinsic retrieval quality is what the product actually experiences, and the two can
  disagree.
- Use Universal Dependencies test treebanks as intrinsic gold data. Report per-language accuracy on
  all tokens and separately on the ambiguous subset, since ambiguity is the default analyzer's known
  weakness.
- Cover `es`, `en`, `pt`, `it`, `de`, `hi` where all candidates apply, plus `ja` and `zh` where
  simplemma has no coverage at all and the comparison is Stanza against nothing.
- Keep the extrinsic probe small and reproducible: a fixed query list with a recorded seed, run in
  `exact`, `lemma` and `auto` modes against the same index.
- Report cost as well as quality: cold-start time, resident memory, tokens per second, and index
  build time. A ten-point accuracy gain that triples indexing time is a different decision from a
  free one.
- The outcome may be a global default change, a per-language mapping, or no change. Record whichever
  it is with its evidence.

## Implementation work

1. Write a versioned experiment configuration naming analyzer versions, UD treebank revisions,
   languages, the query list, the corpus snapshot, and the random seed.
2. Add a probe script that runs each candidate over the UD test files and reports accuracy overall,
   on ambiguous forms, and on out-of-vocabulary forms, with per-language confusion examples.
3. Add a lexicon builder for the wiktextract candidate that extracts form to lemma and POS mappings
   for the tested languages, records the dump revision and license, and reports coverage.
4. Build one index per candidate analyzer over the same cached transcripts and record build time,
   index size, lemma-key counts, and form-lexicon size.
5. Run the fixed query list in each mode and report, per candidate: lemma-only match counts, recall
   gain over exact mode, and a human-reviewed precision estimate on a sampled set of lemma-only
   matches.
6. Measure runtime cost on the same machine and record hardware and software versions.
7. Write a dated report under `experiments/analyzer-comparison/` with the method, tables, sampled
   examples of every disagreement class, cost, and the resulting decision.
8. Apply the decision in Plan 03's analyzer selection if the evidence supports a change, or record
   why the default stands.

## Public interfaces and data

- No runtime API change. Any decision surfaces only as the default analyzer mapping and the
  analyzer identity already recorded in index metadata.
- The experiment adds a configuration schema and a report format, plus a reusable
  lemmatization-accuracy scorer that later plans may call.
- UD treebanks and wiktextract dumps are external inputs; record their revision and license. Derived
  lexicons stay local unless their license clearly permits redistribution.

## Acceptance tests and verification

- Scorer unit tests cover exact matches, case and diacritic differences, multi-word tokens,
  unanalyzable tokens, and empty input.
- The experiment reruns from its configuration and reports missing treebanks or absent optional
  analyzers without silently shrinking the language set.
- Both intrinsic and extrinsic sections are present for every language where the candidate applies,
  and every aggregate can be traced to per-item rows.
- The report distinguishes a lemma match that is linguistically wrong from one that is correct but
  unhelpful for a learner; only the first is a lemmatization error.
- The recorded decision names the analyzer per language and the evidence behind it.

## Non-goals

- Training a lemmatizer, adding a fourth toolkit, or benchmarking every UD system.
- Judging learner usefulness of retrieved clips; Plan 11 owns human relevance labels.
- Making analyzer accuracy a gate on shipping retrieval; exact mode never depends on this work.
