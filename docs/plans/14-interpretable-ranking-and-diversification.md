# Plan 14: Interpretable ranking and diversification

**Status:** Planned

**Depends on:** Plans 04 and 13

## Outcome

Replace the opaque hand score with a reproducible feature table and an interpretable logistic
baseline for `usable-or-better`, then add a simple diversity pass when it improves the result set.
Every ranking experiment remains comparable with deterministic and random baselines.

## Current state

- Ranking combines a few deterministic signals directly in search code.
- Features are not stored/versioned, so experiments cannot reproduce a score or separate general
  segment quality from query-specific match quality.
- No labeled benchmark measures top-result quality or source diversity.

## Decisions

- Keep two compact feature groups: query-independent segment features stored once and query-dependent
  features computed for a request. Version feature definitions and missing-value behavior.
- Seed text features from GDEX's typicality, informativeness, intelligibility, naturalness, and
  self-containment principles, adapting them to spoken clips rather than copying corpus-sentence
  formulas blindly.
- Include what is already useful before adding models: length, commonness, context sufficiency,
  boundary/caption provenance, match type, target position, repetition, and source identity.
  Alignment coverage, ASR agreement, speaking rate, VAD speech ratio, and speech-quality estimates
  are optional inputs only when their plans have produced trustworthy values.
- Train a regularized logistic regression for the binary target `usable` or `excellent`. Preserve
  calibrated probability, coefficient, and per-result contribution inspection.
- Start diversification with a transparent per-video/channel cap or penalty. Consider MMR or another
  method only if the simple rule leaves a measured problem.
- Compare at least deterministic, reproducible random, logistic, and diversity-aware variants using
  Precision@K, NDCG@K, bad-at-K, and source diversity.

## Implementation work

1. Specify a feature registry/table with name, version, type, computation stage, missing behavior,
   and provenance. Migrate the existing deterministic signals into it first.
2. Compute and persist query-independent features by stable segment ID and pipeline version; compute
   match-dependent features from the analyzed query/result without mutating the corpus.
3. Add small, independently optional acoustic feature functions: speaking rate, Silero VAD speech
   ratio, and a DNSMOS-like estimate only after a sample review confirms they mean something for
   this task.
4. Build leakage-safe training/evaluation matrices from Plan 13 labels, fit a seeded regularized
   logistic model, and save the configuration, coefficients, preprocessing, dataset version, and
   metrics.
5. Add `ranked` variants for the existing deterministic score and promoted logistic model. Keep
   reproducible `random` as a first-class control.
6. Add the simplest useful diversity reranker and measure quality/diversity trade-offs across
   queries rather than relying on attractive anecdotes.
7. Provide a diagnostic explanation containing raw values and additive feature contributions. Keep
   verbose explanations opt-in so normal responses stay compact.
8. Publish a dated experiment report and select the default only when held-out results support it;
   otherwise retain the existing deterministic baseline.

## Public interfaces and data

- Stored feature rows name the segment, feature-set version, values, missing indicators, and source
  pipeline versions. Model files name the dataset split and training configuration.
- Search keeps `order="ranked"|"random"`; an optional configured ranker selects the ranked strategy
  without changing caller vocabulary.
- Debug output can return raw features, coefficients, and contributions; public result score and
  rank remain stable fields.

## Acceptance tests and verification

- Unit tests cover every core feature, missing optional audio/alignment data, deterministic
  extraction, coefficient application, seeded random order, and diversity tie-breaking.
- Train/test construction proves no query/video leakage and never trains on the human-only test set.
- The experiment reports Precision/NDCG@K, bad-at-K, and video/channel diversity with bootstrap or
  per-query uncertainty, plus qualitative wins and regressions.
- Search without optional acoustic dependencies still ranks all candidates and explains missing
  values honestly.
- The default changes only when the report shows a useful held-out improvement without an important
  rise in bad examples near the top.

## Non-goals

- Online learning, personalization, opaque composite “audio quality” truth, or mandatory acoustic
  models.
- Treating GDEX formulas or external quality estimates as universal labels.
- Complex diversity optimization before a simple source-aware rule is measured.
