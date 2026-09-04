# Plan 13: Learned multilingual reranker

**Status:** Planned

**Depends on:** Plans 11 and 12

## Outcome

Establish whether a neural reranker beats the interpretable baseline on this task, using human gold
plus lower-weight provenance-marked silver labels. Promote it only when it improves held-out ranking
without placing more bad examples near the top; otherwise publish the negative result and keep the
logistic ranker.

## Current state

- Plan 12 supplies an auditable feature baseline and a leakage-safe evaluation harness.
- Plan 11 supplies a small human gold set, an optional calibrated judge, and a larger written-example
  distant-supervision set.
- Human labels will initially be few and Spanish-heavy, making an unconstrained model comparison
  likely to overfit.

## Decisions

- Measure an **off-the-shelf multilingual cross-encoder zero-shot first**, as the baseline any
  fine-tuned model must beat. A modern reranker may already outrank the logistic model with no
  training at all, and knowing that changes what is worth building. Candidates:
  [`BAAI/bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3), open-licensed and
  strongly multilingual, and
  [`jina-reranker-v2-base-multilingual`](https://jina.ai/models/jina-reranker-v2-base-multilingual/),
  smaller and substantially faster.
- Then fine-tune the smallest model that clears that bar. If reranker-sized models are too heavy for
  the available label volume or latency budget, fall back to a compact encoder such as
  `multilingual-e5-small` (about 118M parameters) with a pairwise classification or regression head.
  Confirm the licenses and current model quality at implementation time rather than trusting this
  note.
- Fine-tune the three-class judgment task or an ordinal equivalent, then derive a ranking score that
  rewards `excellent` and penalizes `bad`.
- Consider a two-stage schedule: pretrain on Plan 11's distant-supervision data, then fine-tune on
  human labels. This is the standard remedy for a small gold set and is worth reporting either way.
- Human labels receive full weight. Calibrated judge labels may expand training with lower sample
  weight and explicit provenance; development and test headline results remain human-only.
- Compare against Plan 12 under identical candidate pools and splits. Evaluate overall and by
  language and query type when sample sizes support a meaningful reading.
- Keep artifacts external by default because of size, but permit a licensed model, config or result
  to be published when practical. Runtime loading is optional and falls back cleanly to the logistic
  ranker.
- Use a qualitative promotion gate: repeatable held-out NDCG improvement and no meaningful increase
  in bad examples near the top. Do not invent a percentage before seeing benchmark variance.

## Implementation work

1. Freeze dataset and split versions and write a single reproducible configuration covering base
   model revision, input format, seed, optimization, label weights, and hardware/software.
2. Run the zero-shot cross-encoder comparison against Plan 12's baseline on the development set and
   record it as its own result row before any training.
3. Encode query, matched form, source sentence, and a small provenance marker without leaking video
   identity or target labels. Document truncation and non-Latin handling.
4. Train the variants that the zero-shot result justifies — human-only, human plus lower-weight
   judge, and optionally distant-supervision pretraining — within a modest compute budget. Save
   checkpoints only when needed for comparison.
5. Calibrate score outputs on human development data and run the unchanged human-only test once for
   the final comparison.
6. Integrate the selected checkpoint behind an optional `ranking-ml` dependency and configuration
   with batched candidate inference. A load or inference failure uses the interpretable ranker and
   records why.
7. Produce a model card with intended use, languages and data, label provenance, licensing, metrics,
   limitations, environmental and compute notes, and artifact retrieval and checksum details.
8. Publish the experiment configuration and report whether promoted or rejected, including per-query
   failures and the measured effect of judge and distant supervision.

## Public interfaces and data

- The HTTP and Python search contract does not change; configured ranked search may report the ranker
  name and version in provenance and diagnostics.
- Model metadata identifies base revision, dataset and splits, training code and config, label-source
  mix, and output calibration.
- No model download occurs during ordinary search or package installation. Explicit model setup and
  `doctor` commands report availability.

## Acceptance tests and verification

- Data-loader tests preserve split boundaries and weights and reject unlabeled provenance.
- A tiny fixture trains, loads and scores deterministically enough for CI without downloading the
  real model; the full experiment is a separately recorded command.
- The report compares deterministic, random, logistic, zero-shot cross-encoder, fine-tuned, and
  best-plus-diversity rankings on the same human-only test candidates and metrics.
- Optional inference batches candidates and falls back to the logistic baseline on missing weights,
  unsupported runtime, or recoverable model failure.
- Latency of the promoted option is measured on the target hardware and stated next to its quality
  gain.
- Promotion or rejection and the evidence behind it are recorded in the roadmap and model card.

## Non-goals

- A large generative ranker, an online serving dependency, an architecture sweep, or claiming broad
  multilingual quality from a small Spanish-heavy test.
- Training on the human-only test set or presenting judge or distant labels as gold.
- Requiring model weights for core package installation or exact retrieval.
