# Plan 12: Ranking features and diversification

**Status:** Planned

**Depends on:** Plans 03 and 11

## Outcome

Replace the opaque hand score with a reproducible feature table and an interpretable logistic
baseline for `usable-or-better`, then add a simple diversity pass when it improves the result set.
Every ranking experiment remains comparable with deterministic and random baselines.

The first version needs no audio and no models: text, metadata and match features are enough for a
useful ranker, and the acoustic signals from Plan 09 join later as optional inputs.

## Current state

- Ranking combines a few deterministic signals directly in search code: the `quality_score`
  computed in `captions.py` from boundary reason, duration, token count and caption kind, and the
  accent-exactness and centrality adjustments applied in `search.py`.
- Those signals are not stored or versioned, so an experiment cannot reproduce a score or separate
  general segment quality from query-specific match quality.
- Plan 11 supplies human labels, leakage-safe splits, and an optional distant-supervision set.

## Decisions

- Structure the features around the distinction that matters for this product:
  - **query-independent segment features**, stored once per segment: how good is this clip as an
    example at all — length, self-containment, context sufficiency, commonness of its vocabulary,
    boundary reason and confidence, caption provenance, repetition;
  - **query-dependent match features**, computed per request: match type (exact or lemma), matched
    surface and lemma, position of the target within the sentence, number of occurrences, accent
    exactness.
  This split is also what makes precomputation possible and what lets distant supervision from
  written dictionary examples inform the first group without contaminating the second.
- Seed the text features from GDEX's typicality, informativeness, intelligibility, naturalness and
  self-containment principles, adapting them to spoken clips rather than copying corpus-sentence
  formulas blindly.
- Version feature definitions and missing-value behavior. Migrate the existing deterministic signals
  into the registry first so current behavior is reproducible as a baseline row rather than deleted.
- Acoustic and alignment features from Plans 09 and 10 — caption-to-ASR agreement, speaking rate, VAD
  speech ratio, intelligibility estimate, alignment coverage — join the query-independent group only
  where Plan 09's sample review found them trustworthy. Every one of them is optional, and search
  must rank correctly when they are absent.
- Train a regularized logistic regression for the binary target `usable` or `excellent`. Preserve
  calibrated probability, coefficient, and per-result contribution inspection. Interpretability is a
  deliberate requirement here, not a limitation: it is what makes the ranker debuggable and what
  makes the next plan's comparison meaningful.
- Optionally pretrain or initialize feature weights from the distant-supervision set, then fit on
  human labels, and report whether it helped.
- Start diversification with a transparent per-video and per-channel cap or penalty. Consider MMR or
  another method only if the simple rule leaves a measured problem.
- Compare at least deterministic, reproducible random, logistic, and diversity-aware variants using
  Precision@K, NDCG@K, bad-at-K, and source diversity.

## Implementation work

1. Specify a feature registry/table with name, version, type, computation stage, missing behavior,
   and provenance. Migrate the existing `quality_score` and search-time adjustments into it first.
2. Compute and persist query-independent features by stable segment ID and pipeline version; compute
   match-dependent features from the analyzed query and result without mutating the corpus.
3. Wire in the optional acoustic and alignment features behind availability checks, with explicit
   missing indicators rather than imputed zeros.
4. Build leakage-safe training and evaluation matrices from Plan 11 labels, fit a seeded regularized
   logistic model, and save the configuration, coefficients, preprocessing, dataset version, and
   metrics.
5. Add `ranked` variants for the existing deterministic score and the promoted logistic model, and
   keep reproducible `random` as a first-class control.
6. Add the simplest useful diversity reranker and measure the quality/diversity trade-off across
   queries rather than relying on attractive anecdotes.
7. Provide a diagnostic explanation containing raw values and additive feature contributions. Keep
   verbose explanations opt-in so normal responses stay compact.
8. Publish a dated experiment report and change the default only when held-out results support it;
   otherwise retain the existing deterministic baseline.

## Public interfaces and data

- Stored feature rows name the segment, feature-set version, values, missing indicators, and source
  pipeline versions. Model files name the dataset split and training configuration.
- Search keeps `order="ranked"|"random"`; a configured ranker selects the ranked strategy without
  changing caller vocabulary.
- Debug output can return raw features, coefficients, and contributions; the public result score and
  rank remain stable fields.

## Acceptance tests and verification

- Unit tests cover every core feature, missing optional audio/alignment data, deterministic
  extraction, coefficient application, seeded random order, and diversity tie-breaking.
- A migration test proves the registry reproduces today's ranking exactly for the current corpus, so
  the baseline is a measured row and not a memory.
- Train/test construction proves no query or video leakage and never trains on the human-only test
  set.
- The experiment reports Precision/NDCG@K, bad-at-K, and video/channel diversity with bootstrap or
  per-query uncertainty, plus qualitative wins and regressions.
- Search without optional acoustic dependencies still ranks all candidates and explains missing
  values honestly.
- The default changes only when the report shows a useful held-out improvement without an important
  rise in bad examples near the top.

## Non-goals

- Online learning, personalization, opaque composite "audio quality" truth, or mandatory acoustic
  models.
- Treating GDEX formulas or external quality estimates as universal labels.
- Complex diversity optimization before a simple source-aware rule is measured.
- Neural ranking, which Plan 13 owns and which must beat this baseline to ship.
