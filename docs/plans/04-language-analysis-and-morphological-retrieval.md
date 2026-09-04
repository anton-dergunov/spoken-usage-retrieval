# Plan 04: Language analysis and morphological retrieval

**Status:** Planned

**Depends on:** Plan 02

## Outcome

Find an inflected form from its dictionary form and vice versa while retaining the current exact
surface behavior. The same interface must also provide real word segmentation for languages where
the current Unicode-regex tokenizer cannot separate words.

## Current state

- Indexing stores accent-folded contiguous 1–5-token surface n-grams.
- Search cannot connect `casa` with `casas`, `estar` with `estoy`, or gender/number variants.
- The regex tokenizer treats long CJK runs as one token and search-time analysis is not versioned.
- Current phrase retrieval is valuable and deterministic; morphology must not replace it or obscure
  whether a result was an exact occurrence.

## Decisions

- Define a small `TextAnalyzer` protocol before integrating a toolkit. It returns ordered tokens
  carrying surface text, normalized surface, lemma, UPOS, morphological features, and original
  character offsets, plus analyzer/model identity.
- Ship a dependency-free Unicode fallback analyzer for exact retrieval. Provide Stanza as the first
  optional analyzer because its UD pipelines cover tokenization, multi-word-token expansion, POS,
  morphology, and lemmatization across [more than 70 languages](https://stanfordnlp.github.io/stanza/).
- Model files are explicit local resources. Indexing never downloads them implicitly; a `models
  download <language>` command installs a known compatible model into the configured directory.
- Store surface and lemma n-gram keys separately. `exact` searches only surface keys, `lemma`
  searches lemma keys, and `auto` unions both with exact occurrences ranked first.
- Query expansion combines the analyzer's query lemma with observed surface-to-lemma mappings in
  the index. This lets a context-poor query such as an isolated inflection recover corpus lemmas
  while exposing ambiguity rather than guessing one hidden analysis.
- Phrase lemma matching remains contiguous and ordered in this milestone. Gaps, dependency
  templates, semantic paraphrases, and separable-verb logic are later research.

## Implementation work

1. Add small analyzed-token/result types and choose an analyzer by BCP-47 primary language.
   Preserve offsets through multi-word expansion and report invalid reconstructed spans clearly.
2. Implement and test the fallback analyzer, then add Stanza behind an `nlp` optional dependency.
   Construct only `tokenize,mwt,pos,lemma`, reuse one pipeline per language, disable implicit model
   downloads, and bound batch/memory use.
3. Record analyzer name, package/model version, and settings in cache/index metadata. Require a
   reindex when a known incompatible analyzer version is selected; add checksum/registry machinery
   only if real model distribution makes it useful.
4. Evolve occurrences with surface key, lemma key, UPOS/morphology, and token offsets. Add a
   language-scoped form lexicon mapping normalized observed forms to candidate lemma/UPOS pairs and
   their frequencies.
5. Analyze source segments once during indexing. Build contiguous lemma n-grams only when every
   member has a usable lemma, retaining surface text and character offsets for display.
6. Implement query analysis and `exact|lemma|auto`. Deduplicate an occurrence found by both routes,
   preserve the strongest match type, and return all candidate query analyses used.
7. Add result fields `match_type`, `matched_surface`, `matched_lemma`, token analysis, and analyzer
   provenance. Add per-mode totals so callers can distinguish exact inventory from expansions.
8. Make suggestions use analyzed language tokens and language-specific stopword resources without
   hiding valid query results.

## Public interfaces and data

```python
corpus.search(
    "casas",
    source_language="es",
    match_mode="auto",  # exact | lemma | auto
    limit=20,
)
```

- `auto` works without Stanza by returning exact-only results and an explicit
  `morphology_available: false`; `lemma` returns a typed unsupported-analysis error when its model is
  absent.
- HTTP uses the same `language` and `match_mode` vocabulary and error semantics.
- Existing source text and highlight offsets remain canonical; lemmas never replace displayed text.

## Acceptance tests and verification

- Spanish tests cover `casa/casas`, adjective gender/number, and `estar/estoy/estaba` in both query
  directions, including an ambiguous surface form.
- English tests cover noun plural and irregular verb/base-form retrieval.
- CJK analyzer tests prove token offsets and phrase matching over non-whitespace input.
- Exact-mode regression tests reproduce current accent-aware ranking and 1–5-gram behavior.
- Rebuilding with the same analyzer is deterministic; changing to an incompatible analyzer produces a
  clear reindex requirement.
- Performance is measured on the seeded corpus and documented; analyzer initialization is not paid
  once per segment or once per request.

## Non-goals

- Claiming every inflection shares a meaning or automatically selecting an intended word sense.
- Fuzzy spelling, semantic retrieval, discontinuous phrases, derivational morphology, or translation.
- Bundling Stanza model weights in the wheel or repository.
- Making lemma matches indistinguishable from exact evidence.
