# Plan 07: Subtitle reliability benchmark

**Status:** Planned

**Depends on:** Plan 06

## Outcome

Run a reproducible, stratified study of manual and automatic source captions against a recorded ASR
baseline, then document when captions should be trusted, scored, verified, or replaced. A useful
negative result is a valid outcome.

## Current state

- Both authored and automatic captions feed the same index without a measured reliability estimate.
- Cue boundaries and transcript errors can each harm retrieval, but the current corpus does not
  distinguish or quantify those failure modes.
- Audio and transcript provenance from Plans 05–06 make a bounded comparison possible.

## Decisions

- Treat this as an experiment before adding production ASR. Use configurable Whisper `large-v3` as
  the recorded reference baseline for the one-off run, not as unquestioned ground truth.
- Stratify a manageable sample by language, channel, caption provenance, speech style, and obvious
  acoustic conditions. Start with Spanish; add another language only if it tests a real hypothesis.
- Report WER for whitespace-tokenized languages and CER for languages such as Chinese and Japanese.
  Preserve both normalized and readable examples behind every aggregate.
- Add human qualitative tags for meaning-changing errors, missing/extra speech, names, disfluencies,
  and boundary problems. Metrics alone do not decide the policy.
- Choose policies from observed error patterns rather than setting universal quality thresholds in
  advance.

## Implementation work

1. Write a versioned experiment configuration listing sampled segment/video IDs, random seed,
   strata, audio preparation, ASR model/settings, and text normalization.
2. Run ASR over fixed clips or videos and save hypotheses, timings, model provenance, runtime, and
   failures as replaceable experiment outputs.
3. Align caption and ASR text for scoring and calculate WER or CER with transparent normalization.
4. Add a small review sheet or reuse Plan 13's format if it already exists; label semantic and
   boundary discrepancies with notes for representative cases.
5. Report distributions and examples by language, channel, caption provenance, and speech/acoustic
   category. Keep per-item results so aggregates can be audited.
6. End with an explicit recommendation for each tested source class: use directly, attach a score,
   verify selectively, replace with ASR, or collect more evidence.

## Public interfaces and data

- This session adds experiment configuration/result schemas, not a production API.
- Each row identifies source language, video/segment, caption provenance, reference provenance,
  normalization version, error metric, qualitative tags, and reviewer status.
- Reports may be published with licensed snippets or identifiers/aggregates as appropriate; do not
  discard useful research output merely because the source media stays local.

## Acceptance tests and verification

- Metric tests cover insertions, deletions, substitutions, empty text, diacritics, and CJK
  character scoring.
- The experiment is rerunnable from its configuration and records missing media/model failures
  without changing the sample silently.
- Results contain both manual and automatic captions from multiple channels and state any important
  gaps in the intended strata.
- The report separates ASR disagreement from human-confirmed caption error and records the policy
  decision with supporting examples.

## Non-goals

- Declaring ASR output to be perfect transcription or benchmarking every ASR model.
- Shipping continuous ASR verification before the experiment demonstrates value.
- Producing a statistically universal study of YouTube caption quality.
