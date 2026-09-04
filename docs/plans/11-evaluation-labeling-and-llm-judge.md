# Plan 11: Evaluation, labeling, and LLM judge

**Status:** Planned

**Depends on:** Plans 06 and 07

## Outcome

Create a compact human-gold benchmark for spoken examples and a local review workflow. Calibrate an
optional structured LLM judge against those labels for faster experiments, while keeping human-only
held-out results as the headline evidence.

**Start this early.** It depends only on the packaged player and a running service, not on audio,
alignment or translation, because a human judges a clip by watching it. Collecting a few hundred
judgments is limited by the owner's calendar, not by code, so labeling should proceed in parallel
with the Stage 3 viewer work.

## Current state

- Retrieval tests establish deterministic behavior but do not measure whether a clip is genuinely
  useful to a learner.
- There is no versioned judgment format, label guidance, split policy, or review interface.
- Public resources measure related lexical or sentence properties, not this project's full
  query-in-authentic-speech judgment.
- Plan 06 exports a player with a blind mode and keyboard operation, which is the review tool's core.

## Decisions

- Label each query–clip pair `bad`, `usable`, or `excellent`. Add multi-select flags for boundary
  error, transcript mismatch, unclear/fast/overlapping speech, noise, context dependence, difficulty,
  named entities, and near duplication, plus an optional note.
- Target 30–50 Spanish queries and 15–20 candidates per query after a smaller pilot establishes that
  the rubric works. Include words and phrases across frequencies, forms, registers, and failure
  modes; do not inflate the dataset before reviewing label consistency.
- Split by query and video so near-identical occurrences cannot leak across train/development/test.
  Freeze a human-only test set before using judge labels.
- Judge the clip as played, not the transcript as written. Speech clarity flags are set from
  listening, which is why local audio is not required.
- Use an LLM judge only offline with schema-constrained output, prompt/model provenance, and
  calibration against repeated human labels. Judge-generated labels are visibly marked and never
  replace the human headline benchmark.
- Treat related public datasets as optional research inputs, not mandatory dependencies. The
  [original GDEX criteria](https://euralex.org/elx_proceedings/Euralex2008/095_Euralex_2008_Adam%20Kilgarriff_Milos%20Husak_Katy%20McAdam_Michael%20Rundell_Pavel%20Rychly_GDEX_Automatically%20Finding_Good_Di.pdf)
  and [later human evaluation](https://aclanthology.org/2024.lrec-main.1538/) inform the rubric.
  CompLex/MultiLS-style resources can inform difficulty but cannot stand in for authentic
  spoken-example labels.

### Distant supervision for training data

Human labels will be too few to train anything beyond a small logistic model, so this plan also
defines a cheap, larger, clearly-marked silver source for Plans 12 and 13:

- Curated dictionary example sentences are positives and random corpus segments containing the same
  headword are negatives. This is the approach in
  [Khan et al. 2021's GDEX experiment](https://onlinelibrary.wiley.com/doi/abs/10.1155/2021/2553199),
  which reached about 77% accuracy on English good-example prediction under distant supervision.
- Candidate sources: Tatoeba sentences (CC-BY 2.0 FR, already the example source for JMdict and
  OpenRussian), Wiktionary usage examples from `kaikki.org` wiktextract dumps that the companion
  Acervo repository already downloads, and the
  [English GDEX dataset](https://github.com/MuhammadYaseenKhan/english-gdex), which stays a local-only
  research candidate until its absent or unclear license is resolved.
- State the domain gap plainly. These are written, curated sentences, so they supervise *general
  example goodness* — the query-independent half of Plan 12's feature split — and not spoken-clip
  usefulness. They are pretraining and feature-validation material, never evaluation data.
- The LLM judge is the second silver source, with its own provenance and lower weight.

## Implementation work

1. Write the rubric with positive/negative examples, flag definitions, and guidance for borderline
   cases. Pilot it on a small shared set and revise once before freezing version 1.
2. Define a versioned judgment record with query, stable segment ID, shown text/timing/provenance,
   label, flags, reviewer pseudonym/ID, timestamp, rubric version, and optional note.
3. Build a local review page using the packaged player: blind ranking scores by default, keyboard
   labeling, replay and context, progress, skip, and export/resume.
4. Generate a reproducible candidate pool containing deterministic, random, and later ranker output
   without silently replacing candidates after labeling begins.
5. Collect the target Spanish benchmark, duplicate a useful subset across reviewers where possible,
   adjudicate important disagreements, and assign leakage-safe splits.
6. Add a provider-injected offline judge command that emits the same label/flag schema plus
   confidence/rationale fields, prompt/model/provider version, and source-label provenance.
7. Measure exact/weighted agreement, per-class precision/recall, confusion, and flag agreement on a
   human-labeled development set and once on held-out data. Record both successful and negative
   calibration findings.
8. Build the distant-supervision dataset from at least one licensed source, with a documented
   extraction method, headword alignment, negative sampling procedure, and size, and measure how well
   its labels correlate with human labels on the overlapping subset.
9. Add a short research-data registry recording each candidate dataset's purpose, source, license
   status, permitted use, and whether it was actually used.

## Public interfaces and data

- Human, judge, and distant-supervision records share identifiers and label vocabularies but require
  `label_source: human|llm|distant` with source-specific provenance.
- The review UI is local research tooling; it may write JSONL or SQLite through a small append-safe
  endpoint and can export a stable dataset snapshot.
- Published benchmark material includes only source content and identifiers that can legally be
  redistributed; a reproducible local build may fill licensed or local-only inputs.

## Acceptance tests and verification

- Schema/rubric tests reject unknown labels, invalid flags, missing provenance, and records whose
  candidate snapshot no longer matches.
- Review UI tests cover play/replay, context, shortcuts, save/resume, skip, progress, blind mode, and
  accidental overwrite prevention.
- The dataset report describes query/candidate selection, label distribution, agreement,
  adjudication, splits, missing data, and known biases.
- Judge evaluation reports agreement and confusion against human labels and never mixes judge rows
  into the human-only headline metric.
- The distant-supervision report states its size, license, extraction method, and measured
  correlation with human labels, including a negative finding if the correlation is weak.
- Every external dataset actually used has a recorded compatible license and provenance.

## Non-goals

- Crowdsourcing infrastructure, a universal pedagogical rubric, or a large multilingual benchmark in
  the first pass.
- Using LLM or distant labels online, hiding them as human gold, or requiring an API key for
  evaluation.
- Treating lexical difficulty data or written dictionary examples as direct spoken-clip relevance
  labels.
