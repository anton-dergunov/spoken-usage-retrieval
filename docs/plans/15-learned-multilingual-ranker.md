# Plan 15: Learned multilingual ranker

**Status:** Planned

**Depends on:** Plans 13 and 14

## Outcome

Fine-tune and evaluate one small multilingual query–sentence ranker, using human gold plus
lower-weight provenance-marked judge labels. Promote it only when it improves held-out ranking
without placing more bad examples near the top; otherwise publish the negative result and keep the
interpretable baseline.

## Current state

- Plan 14 supplies an auditable feature baseline and leakage-safe evaluation harness.
- Human labels will initially be small and Spanish-heavy, making an unconstrained model comparison
  likely to overfit.
- Optional public difficulty/GDEX data is related but does not directly express spoken query–clip
  usefulness.

## Decisions

- Begin with one widely supported compact multilingual encoder suitable for query/sentence pair
  classification, initially `distilbert-base-multilingual-cased`. Reconsider it only if licensing,
  maintenance, or a small untuned comparison reveals a clear blocker.
- Fine-tune the three-class judgment task or an ordinal equivalent, then derive a ranking score that
  rewards `excellent` and penalizes `bad`.
- Human labels receive full weight. Calibrated judge labels may expand training with lower sample
  weight and explicit provenance; development and test headline results remain human-only.
- Compare against Plan 14 under identical candidate pools and splits. Evaluate overall and by
  language/query type when sample sizes support a meaningful reading.
- Keep artifacts external by default because of size, but permit a licensed model/config/result to
  be published when practical. Runtime loading is optional and falls back cleanly to the logistic
  ranker.
- Use a qualitative promotion gate: repeatable held-out NDCG improvement and no meaningful increase
  in bad examples near the top. Do not invent a percentage before seeing benchmark variance.

## Implementation work

1. Freeze dataset/split versions and write a single reproducible training configuration covering
   base model revision, input format, seed, optimization, label weights, and hardware/software.
2. Encode query, matched form, source sentence, and a small provenance marker without leaking video
   identity or target labels. Document truncation and non-Latin handling.
3. Train human-only and human-plus-lower-weight-judge variants within a modest compute budget. Save
   checkpoints only when needed for comparison.
4. Calibrate score outputs on human development data and run the unchanged human-only test once for
   the final comparison.
5. Integrate the selected checkpoint behind an optional `ranking-ml` dependency/configuration and
   batch candidate inference. A load/inference failure uses the interpretable ranker and records why.
6. Produce a model card with intended use, languages/data, label provenance, licensing, metrics,
   limitations, environmental/compute notes, and artifact retrieval/checksum details.
7. Publish the experiment configuration and report whether promoted or rejected, including
   per-query failures and the effect of judge supervision.

## Public interfaces and data

- The HTTP/Python search contract does not change; configured ranked search may report the ranker
  name/version in provenance and diagnostics.
- Model metadata identifies base revision, dataset/splits, training code/config, label-source mix,
  and output calibration.
- No model download occurs during ordinary search or package installation. Explicit model setup and
  `doctor` commands report availability.

## Acceptance tests and verification

- Data-loader tests preserve split boundaries and weights and reject unlabeled provenance.
- A tiny fixture trains/loads/scores deterministically enough for CI without downloading the real
  model; the full experiment is a separately recorded command.
- The report compares deterministic, random, logistic, learned, and learned-plus-diversity rankings
  on the same human-only test candidates and metrics.
- Optional inference batches candidates and falls back to the logistic baseline on missing weights,
  unsupported runtime, or recoverable model failure.
- Promotion/rejection and the evidence behind it are recorded in the roadmap/model card.

## Non-goals

- A large generative ranker, online serving dependency, architecture sweep, or claiming broad
  multilingual quality from a small Spanish-heavy test.
- Training on the human-only test set or presenting judge labels as gold.
- Requiring model weights for core package installation or exact retrieval.
