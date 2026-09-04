# Remaining-work roadmap

This directory is the canonical implementation roadmap for Spoken Usage Retrieval. Each numbered
document is intended to be executable in a focused development session. A session may refine its
plan when new evidence appears, but it must preserve the decisions and acceptance criteria recorded
here unless the roadmap is deliberately revised.

The repository already has a working Spanish subtitle-only baseline: channel discovery, cached
YouTube captions, deterministic segmentation and 1–5-gram retrieval, a FastAPI surface, and a React
clip viewer. The remaining work turns that prototype into a public, multilingual, reusable service
and then adds the evaluation and ML work that makes it a meaningful IR portfolio project.

## How to use these plans

1. Take the first `Planned` item whose dependencies are complete.
2. Re-read the named current implementation and any linked experiment before editing code.
3. Keep optional heavyweight capabilities behind extras and preserve the no-model baseline.
4. Implement the whole acceptance boundary; do not silently leave a partial public contract.
5. Run the plan's verification and update its status and this index in the same change.

In addition to plan-specific checks, run the standard Python tests, web tests, and production web
build whenever a session touches those surfaces. Documentation-only and isolated experiment
sessions need only the checks relevant to what they changed.

## Pragmatism rule

Prefer the smallest implementation that enables a useful product path or a meaningful experiment.
Do not add abstractions, policy engines, validation layers, background infrastructure, compatibility
machinery, or configurable knobs in anticipation of needs that have not appeared. Start with a
clear baseline, measure it, and expand it only when the next experiment or integration requires it.

Keep secrets out of Git. Treat large or copyrighted inputs cautiously, but do not impose blanket
publication bans: small fixtures, derived data, labels, reports, and model artifacts may be tracked
when their license permits it and doing so materially improves reproducibility. Record the source,
license, and reason instead of building elaborate preventive machinery.

Statuses are `Planned`, `In progress`, `Blocked`, and `Complete`. Experimental decision gates can
finish with a documented negative result; they do not have to justify shipping a model.

## Ordered plans

### Portfolio-ready baseline

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [01 · Public repository foundations](01-public-repository-foundations.md) | Planned | — | License, packaging, CI, contribution guidance, and pragmatic data hygiene. |

Completing Plan 01 is the first public-GitHub checkpoint. It does not wait for the research roadmap.

### Multilingual retrieval

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [02 · Multilingual corpus model](02-multilingual-corpus-model.md) | Planned | 01 | Language-neutral configuration, cache, index, API, and demo state. |
| [03 · Channel catalogue expansion](03-channel-catalogue-expansion.md) | Planned | 02 | Repeatable, evidence-based catalogues for the other nine high-demand languages. |
| [04 · Language analysis and morphological retrieval](04-language-analysis-and-morphological-retrieval.md) | Planned | 02 | Exact and lemma-sequence retrieval through a pluggable analyzer. |

Plans 03 and 04 may run in parallel after Plan 02. Plan 03 is one template containing nine separate
catalogue sessions; languages are not required to be curated in a single change.

### Corpus and service readiness

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [05 · Authored caption-track acquisition](05-authored-caption-track-acquisition.md) | Planned | 02 | Canonical source captions plus safe creator-authored translation tracks. |
| [06 · Audio cache and media preparation](06-audio-cache-and-media-preparation.md) | Planned | 05 | Optional audio-only cache and reproducible analysis clips. |
| [07 · Subtitle reliability benchmark](07-subtitle-reliability-benchmark.md) | Planned | 06 | Evidence-based policy for trusting, verifying, or replacing captions. |
| [08 · Audio forced alignment](08-audio-forced-alignment.md) | Planned | 06, 07 | Confidence-bearing source-text timing with safe cue-level fallback. |
| [09 · Incremental background indexing](09-incremental-background-indexing.md) | Planned | 04–08 | Idempotent scheduled ingestion and prioritized derived work. |
| [10 · Library, CLI, and service API](10-library-cli-and-service-api.md) | Planned | 02, 04, 09 | Stable Python, command-line, and versioned HTTP contracts. |
| [11 · Query-time translation and semantic alignment](11-query-time-translation-and-semantic-alignment.md) | Planned | 05, 08, 10 | Optional one-call translation enrichment in any target language. |
| [12 · Reusable React player](12-reusable-react-player.md) | Planned | 10, 11 | A locally packable host-independent player and client. |

Completing Plan 12 is the integration-readiness checkpoint. The service remains a foreground
process; its eventual host owns Docker, systemd, or other process supervision.

### Evaluation and ML showcase

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [13 · Evaluation, labeling, and LLM judge](13-evaluation-labeling-and-llm-judge.md) | Planned | 07, 08, 12 | Human gold judgments and a separately calibrated offline judge. |
| [14 · Interpretable ranking and diversification](14-interpretable-ranking-and-diversification.md) | Planned | 04, 13 | Reproducible features, logistic ranking, explanations, and diversity metrics. |
| [15 · Learned multilingual ranker](15-learned-multilingual-ranker.md) | Planned | 13, 14 | Optional small multilingual ranker with a promotion gate and model card. |

The human-only held-out set remains the headline evaluation even when judge labels or public
auxiliary data are used for training.

### Host integration and release

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [16 · Acervo integration and release](16-acervo-integration-and-release.md) | Planned | 10–15 | Package smoke tests, Acervo wiring checklist, and final public release evidence. |

The retrieval repository owns reusable packages and foreground commands. Acervo owns its Compose
service, persistent-volume wiring, capture pipeline, article storage, and modal presentation.

## Locked cross-plan contracts

- Every stored corpus object names its BCP-47 source language, caption provenance, analyzer/model
  version, and stable segment/object identity where applicable. A target language exists only on
  derived translation requests.
- `Corpus.search` accepts `source_language`, `match_mode`, `order`, `limit`, and an optional random
  seed. Exact search works without external models; `auto` adds morphology when it is available.
- Search results expose a stable segment ID, matched surface and offsets, match type, source
  provenance, video/channel metadata, timing, and ranking details.
- The command line runs in the foreground. `serve`, `update --once`, `search`, `channels`, `status`,
  `reindex`, `models`, and `doctor` are reusable commands, not a private daemon manager.
- HTTP routes are versioned under `/api/v1`; mutable management routes are opt-in and protected by
  a simple operator token when enabled.
- Translation is an asynchronous per-clip enrichment. It never blocks source playback or corpus
  indexing and performs at most one LLM generation call for a cache miss.
- `@spoken-usage-retrieval/react` exports a player, typed client, and styles. The host owns the
  modal, navigation, and persistence decisions.
- Credentials remain local and untracked. Other artifacts are untracked by default, but may be
  published deliberately when licensing, size, and reproducibility make that reasonable.

## Original-request coverage

| Requirement | Plans |
| --- | --- |
| Public GitHub quality, license, CI, documentation, packaging | 01, 16 |
| Dedicated channel directory and popular-language catalogues | 02, 03 |
| Language-neutral source handling and query-selected translation language | 02, 10, 11, 12 |
| Singular/plural, gender, conjugation, and base-form retrieval in both directions | 04 |
| Candidate limits, random samples, quality ranking, and result diversity | 10, 13–15 |
| Public/hand-labeled data, labeling UI, logistic baseline, small-model fine-tuning | 13–15 |
| Creator-authored subtitles in every available language, excluding auto-translation | 05 |
| Audio download, clarity features, subtitle validation, and ASR fallback | 06, 07, 14 |
| Manual-first and automatic-second forced alignment | 08, 09 |
| Periodic discovery, incremental indexing, pause/resume, health, and statistics | 09, 10 |
| Optional one-call translation, semantic groups, loading, cancellation, and fallback | 11, 12 |
| Reusable Python package, service, CLI, React player, and Acervo integration | 10, 12, 16 |
| Article example provenance, channel display, video modal, and LLM context selection | 16 |

Feasibility findings remain in [`../feasibility-notes.md`](../feasibility-notes.md), while completed
experimental evidence remains in [`../../experiments/index.md`](../../experiments/index.md). Plans
describe future work; experiment reports describe observations.

The original working brief remains in `TODO.txt` for now as a source record. It can be retired in a
later documentation cleanup only after the owner explicitly asks for that.
