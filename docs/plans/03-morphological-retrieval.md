# Plan 03: Morphological retrieval

**Status:** Planned

**Depends on:** Plan 02

## Outcome

Find an inflected form from its dictionary form and vice versa while retaining the current exact
surface behavior. Lemma retrieval works immediately after installation, with no model download, for
every Latin-script catalogue language. The same interface also provides real word segmentation for
languages where the current Unicode-regex tokenizer cannot separate words.

## Current state

- Indexing stores accent-folded contiguous 1–5-token surface n-grams.
- Search cannot connect `casa` with `casas`, `estar` with `estoy`, or gender/number variants.
- The regex tokenizer treats long CJK runs as one token and search-time analysis is not versioned.
- Current phrase retrieval is valuable and deterministic; morphology must not replace it or obscure
  whether a result was an exact occurrence.

## Decisions

- Define a small `TextAnalyzer` protocol before integrating a toolkit. It returns ordered tokens
  carrying surface text, normalized surface, lemma, optional UPOS, optional morphological features,
  and original character offsets, plus analyzer/model identity. This protocol is the seam that keeps
  the toolkit choice cheap to revisit.
- Ship a dependency-free Unicode fallback analyzer for exact retrieval.
- Make [simplemma](https://github.com/adbar/simplemma) the default lemma analyzer. It is MIT
  licensed, pure Python, about 19 MB installed with **no per-language model downloads**, covers 54
  languages at 0.91–0.97 lemma accuracy (`es` 0.93, `pt` 0.94, `hi` 0.95, `en` 0.96, `de` 0.97) and
  processes millions of tokens per second. Lemma mode therefore works out of the box and no
  model-provisioning command is on the critical path.
- Record simplemma's two real limits honestly. It is word-level, so it **cannot disambiguate a
  surface form with several valid lemmas**; expose the ambiguity instead of hiding a guess. And it
  has **no Japanese, Chinese or Korean support**, which are three of the ten target languages, so the
  optional analyzer is an enabling dependency for `ja`/`ko`/`zh`, not merely an accuracy upgrade.
- Provide Stanza behind an `nlp` optional dependency as the second analyzer. Its UD pipelines cover
  [more than 70 languages](https://stanfordnlp.github.io/stanza/) with tokenization, multi-word-token
  expansion, POS, morphology and lemmatization. Model files are explicit local resources: indexing
  never downloads them implicitly, and `models download <language>` installs a known compatible
  model into the configured directory.
- Because the default analyzer produces no UPOS or features, those columns are nullable everywhere
  and no retrieval path may require them. Lemma n-grams are built whenever every member has a lemma,
  with or without POS.
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
2. Implement and test the Unicode fallback analyzer, then the simplemma analyzer as the default for
   its supported languages. Feed it the project's existing tokenizer output so offsets stay canonical
   rather than adopting `simple_tokenizer` spans.
3. Add Stanza behind the `nlp` extra. Construct only `tokenize,mwt,pos,lemma`, reuse one pipeline per
   language, disable implicit model downloads, and bound batch/memory use. Select it automatically
   for languages simplemma does not cover when it is installed, and report an unsupported-analysis
   error when it is not.
4. Record analyzer name, package/model version, and settings in cache/index metadata. Require a
   reindex when a known incompatible analyzer version is selected; add checksum/registry machinery
   only if real model distribution makes it useful.
5. Evolve occurrences with surface key, lemma key, nullable UPOS/morphology, and token offsets. Add a
   language-scoped form lexicon mapping normalized observed forms to candidate lemma/UPOS pairs and
   their frequencies.
6. Analyze source segments once during indexing. Build contiguous lemma n-grams only when every
   member has a usable lemma, retaining surface text and character offsets for display.
7. Implement query analysis and `exact|lemma|auto`. Deduplicate an occurrence found by both routes,
   preserve the strongest match type, and return all candidate query analyses used.
8. Add result fields `match_type`, `matched_surface`, `matched_lemma`, token analysis, and analyzer
   provenance. Add per-mode totals so callers can distinguish exact inventory from expansions.
9. Make suggestions use analyzed language tokens and language-specific stopword resources without
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

- `auto` degrades to exact-only with an explicit `morphology_available: false` when no analyzer
  supports the language; `lemma` returns a typed unsupported-analysis error in that case.
- Results name the analyzer that produced them so a mixed-analyzer corpus stays interpretable.
- HTTP uses the same `language` and `match_mode` vocabulary and error semantics.
- Existing source text and highlight offsets remain canonical; lemmas never replace displayed text.

## Acceptance tests and verification

- Spanish tests cover `casa/casas`, adjective gender/number, and `estar/estoy/estaba` in both query
  directions, including an ambiguous surface form whose candidate analyses are all reported.
- English tests cover noun plural and irregular verb/base-form retrieval.
- A test proves that a language simplemma does not support falls back correctly: exact mode works,
  `auto` reports `morphology_available: false`, and `lemma` errors clearly.
- CJK analyzer tests (skipped without the `nlp` extra) prove token offsets and phrase matching over
  non-whitespace input.
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
- Choosing the final per-language analyzer by intuition; Plan 04 measures that separately.
