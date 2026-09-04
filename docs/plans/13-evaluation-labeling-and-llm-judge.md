# Plan 13: Evaluation, labeling, and LLM judge

**Status:** Planned

**Depends on:** Plans 07, 08, and 12

## Outcome

Create a compact human-gold benchmark for spoken examples and a local review workflow. Calibrate an
optional structured LLM judge against those labels for faster experiments, while keeping human-only
held-out results as the headline evidence.

## Current state

- Retrieval tests establish deterministic behavior but do not measure whether a clip is genuinely
  useful to a learner.
- The README proposes a small benchmark, yet there is no versioned judgment format, label guidance,
  split policy, or review interface.
- Public resources measure related lexical or sentence properties, not this project's full
  query-in-authentic-speech judgment.

## Decisions

- Label each query–clip pair `bad`, `usable`, or `excellent`. Add multi-select flags for boundary
  error, transcript mismatch, unclear/fast/overlapping speech, noise, context dependence, difficulty,
  named entities, and near duplication, plus an optional note.
- Target 30–50 Spanish queries and 15–20 candidates per query after a smaller pilot establishes that
  the rubric works. Include words and phrases across frequencies, forms, registers, and failure
  modes; do not inflate the dataset before reviewing label consistency.
- Split by query and video so near-identical occurrences cannot leak across train/development/test.
  Freeze a human-only test set before using judge labels.
- Use an LLM judge only offline with schema-constrained output, prompt/model provenance, and
  calibration against repeated human labels. Judge-generated labels are visibly marked and never
  replace the human headline benchmark.
- Treat related public datasets as optional research inputs, not mandatory dependencies. The
  [original GDEX criteria](https://euralex.org/elx_proceedings/Euralex2008/095_Euralex_2008_Adam%20Kilgarriff_Milos%20Husak_Katy%20McAdam_Michael%20Rundell_Pavel%20Rychly_GDEX_Automatically%20Finding_Good_Di.pdf)
  and [later human evaluation](https://aclanthology.org/2024.lrec-main.1538/) inform the rubric. The
  [English GDEX dataset](https://github.com/MuhammadYaseenKhan/english-gdex) is only a local research
  candidate until its absent/unclear reusable license is resolved. CompLex/Conplext/MultiLS-style
  resources can inform difficulty but cannot stand in for authentic spoken-example labels.

## Implementation work

1. Write the rubric with positive/negative examples, flag definitions, and guidance for borderline
   cases. Pilot it on a small shared set and revise once before freezing version 1.
2. Define a versioned judgment record with query, stable segment ID, shown text/timing/provenance,
   label, flags, reviewer pseudonym/ID, timestamp, rubric version, and optional note.
3. Build a local review page using the packaged player: blind ranking scores by default, support
   keyboard labeling, replay/context, progress, skip, and export/resume.
4. Generate a reproducible candidate pool containing deterministic, random, and later ranker output
   without silently replacing candidates after labeling begins.
5. Collect the target Spanish benchmark, duplicate a useful subset across reviewers, adjudicate
   important disagreements, and assign leakage-safe splits.
6. Add a provider-injected offline judge command that emits the same label/flag schema plus
   confidence/rationale fields, prompt/model/provider version, and source-label provenance.
7. Measure exact/weighted agreement, per-class precision/recall, confusion, and flag agreement on a
   human-labeled development set and once on held-out data. Record both successful and negative
   calibration findings.
8. Add a short research-data registry recording candidate dataset purpose, source, license status,
   permitted use, and whether it was actually used. Avoid a larger governance system.

## Public interfaces and data

- Human and judge records share identifiers and label/flag vocabularies but require
  `label_source: human|llm` and source-specific provenance.
- The review UI is local research tooling; it may write JSONL or SQLite through a small append-safe
  endpoint and can export a stable dataset snapshot.
- Published benchmark material includes only source content and identifiers that can legally be
  redistributed; a reproducible local build may fill licensed/local-only inputs.

## Acceptance tests and verification

- Schema/rubric tests reject unknown labels, invalid flags, missing provenance, and records whose
  candidate snapshot no longer matches.
- Review UI tests cover play/replay, context, shortcuts, save/resume, skip, progress, and accidental
  overwrite prevention.
- The dataset report describes query/candidate selection, label distribution, agreement,
  adjudication, splits, missing data, and known biases.
- Judge evaluation reports agreement and confusion against human labels and never mixes judge rows
  into the human-only headline metric.
- Every external dataset actually used has a recorded compatible license and provenance.

## Non-goals

- Crowdsourcing infrastructure, a universal pedagogical rubric, or a large multilingual benchmark
  in the first pass.
- Using LLM judgments online, hiding them as human gold, or requiring an API key for evaluation.
- Treating lexical difficulty data as direct spoken-example relevance labels.
