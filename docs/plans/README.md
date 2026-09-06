# Remaining-work roadmap

This directory is the canonical implementation roadmap for Spoken Usage Retrieval. Each numbered
document is intended to be executable in a focused development session. A session may refine its
plan when new evidence appears, but it must preserve the decisions and acceptance criteria recorded
here unless the roadmap is deliberately revised.

The repository already has a working Spanish subtitle-only baseline: channel discovery, cached
YouTube captions, deterministic segmentation and 1–5-gram retrieval, a FastAPI surface, and a React
clip viewer. The remaining work turns that prototype into a public, multilingual, reusable service,
puts it into real use early, and then adds the evaluation and ML work that makes it a meaningful
information-retrieval portfolio project.

## How to use these plans

1. Take the first `Planned` item whose dependencies are complete.
2. Re-read the named current implementation and any linked experiment before editing code.
3. Keep optional heavyweight capabilities behind extras and preserve the no-model baseline.
4. Implement the whole acceptance boundary; do not silently leave a partial public contract.
5. Run the plan's verification and update its status and this index in the same change.

In addition to plan-specific checks, run the standard Python tests, web tests, and production web
build whenever a session touches those surfaces. Documentation-only and isolated experiment
sessions need only the checks relevant to what they changed.

## Sequencing principles

Two rules shape the order, and both were learned by getting the order wrong first.

**Integrate before improving.** Plan 07 puts the service into real use inside Acervo with
source-language playback only — no audio or acoustic models. Host-independent Plan 08 was completed
early without Acervo; the remaining integration-sensitive upgrades stay additive to a system already
carrying real usage, which is a far better position from which to judge what quality work is worth
doing.

**Start human labeling as early as possible.** Plan 11 depends only on the packaged player and a
running service, because a person judges a clip by watching it, not by reading a waveform. Collecting
a few hundred judgments is bounded by calendar time rather than code, so it should run in parallel
with the Stage 3 viewer work rather than after it.

## Pragmatism rule

Prefer the smallest implementation that enables a useful product path or a meaningful experiment.
Do not add abstractions, policy engines, validation layers, background infrastructure, compatibility
machinery, or configurable knobs in anticipation of needs that have not appeared. Start with a
clear baseline, measure it, and expand it only when the next experiment or integration requires it.

Where a plan names a library or model, that is a researched starting point with its licensing and
cost noted, not a commitment. Confirm the current state of the tool when the session begins, and
record the decision either way.

Keep secrets out of Git. Treat large or copyrighted inputs cautiously, but do not impose blanket
publication bans: small fixtures, derived data, labels, reports, and model artifacts may be tracked
when their license permits it and doing so materially improves reproducibility. Record the source,
license, and reason instead of building elaborate preventive machinery.

Statuses are `Planned`, `In progress`, `Blocked`, and `Complete`. Experimental decision gates can
finish with a documented negative result; they do not have to justify shipping a model.

## Ordered plans

### Stage 1 · Public and multilingual foundations

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [01 · Public repository foundations](01-public-repository-foundations.md) | Complete | — | License, packaging, CI, contribution guidance, and pragmatic data hygiene. |
| [02 · Multilingual corpus model](02-multilingual-corpus-model.md) | Complete | 01 | Language-neutral configuration, cache, index, API, and demo state. |
| [03 · Morphological retrieval](03-morphological-retrieval.md) | Complete | 02 | Exact and lemma-sequence retrieval through a pluggable analyzer, with no model download required. |
| [04 · Analyzer comparison experiment](04-analyzer-comparison-experiment.md) | Complete | 03 | Stanza led coverage-adjusted quality in all ten languages; compact token positions saved 85.1% with parity. |
| [04a · Production morphology promotion](04a-production-morphology-promotion.md) | Complete | 03, 04 | Prefer locally available Stanza and use compact token-position retrieval in production. |

Completing Plan 01 is the first public-GitHub checkpoint; it does not wait for the research roadmap.
Plan 04 was optional and blocks nothing; its production recommendations require separate plans.

### Stage 2 · Integration-ready service

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [05 · Library, CLI, and service API](05-library-cli-and-service-api.md) | Complete | 02, 03 | Stable Python, command-line, and versioned HTTP contracts. |
| [06 · Reusable React player](06-reusable-react-player.md) | Complete | 05 | A locally packable host-independent player and typed client used by the demo. |
| [07 · Acervo integration slice](07-acervo-integration.md) | Planned | 05, 06 | The service in real use behind article examples, source playback only. |

Completing Plan 07 is the integration checkpoint. The service remains a foreground process; its host
owns Docker, systemd, or other process supervision.

### Stage 3 · Viewer quality

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [08 · Target-language text](08-target-language-text.md) | Complete | 02, 05, 06 | Authored translation tracks plus optional one-call literal LLM translation, cache warming, and alignment groups. |
| [09 · Audio cache and caption-reliability benchmark](09-audio-and-caption-reliability.md) | Planned | 07 | Optional audio, an evidence-based caption-trust policy, and validated acoustic signals. |
| [10 · Forced alignment](10-forced-alignment.md) | Planned | 09 | Confidence-bearing source-text timing with safe cue-level fallback. |

### Stage 4 · Retrieval science

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [11 · Evaluation, labeling, and LLM judge](11-evaluation-labeling-and-llm-judge.md) | Planned | 06, 07 | Human gold judgments, a review tool, a calibrated offline judge, and a distant-supervision set. |
| [12 · Ranking features and diversification](12-ranking-features-and-diversification.md) | Planned | 03, 11 | Reproducible features, logistic ranking, explanations, and diversity metrics. |
| [13 · Learned multilingual reranker](13-learned-multilingual-reranker.md) | Planned | 11, 12 | A zero-shot and fine-tuned neural comparison with a promotion gate and model card. |

The remaining work in Stages 3 and 4 forms parallel tracks after Plan 07; Plan 08 is the deliberate
host-independent exception. Plan 11's labeling should begin as soon as Plan 07 lands. Plan 12 ships with text and metadata
features alone — Plan 09's acoustic features and Plan 10's alignment coverage are optional later
inputs, used only where Plan 09's sample review found them trustworthy. The human-only held-out set
remains the headline evaluation even when judge or distant labels are used for training.

### Stage 5 · Scale and release

| Plan | Status | Depends on | Outcome |
| --- | --- | --- | --- |
| [14 · Incremental background indexing](14-incremental-background-indexing.md) | Planned | 05, 08–10 | Idempotent scheduled ingestion and prioritized derived work. |
| [15 · Release and public evidence](15-release-and-public-evidence.md) | Planned | 07, 14 | Package smoke tests, reconciled documentation, and final public release evidence. |

Plan 14 is late on purpose: a scheduled `speech-retrieval update --once` keeps a small corpus fresh,
so job scheduling is a scaling improvement rather than a prerequisite. Start it when full rebuilds
become slow enough to hurt.

## Language catalogues

Curating sources for a new language is one reviewable content session per language, repeated as
often as wanted, so it is a template rather than a numbered plan:
[`templates/language-catalogue-session.md`](templates/language-catalogue-session.md). It is runnable
any time after Plan 02, though a language is only properly searchable once Plan 03 has an analyzer
for it.

| Language | Catalogue file | Status | Report |
| --- | --- | --- | --- |
| Spanish (`es`) | `config/channels/es.json` | Complete; four starter channels enabled | [`docs/design.md`](../design.md) |
| English (`en`) | — | Not started | — |
| French (`fr`) | — | Not started | — |
| German (`de`) | — | Not started | — |
| Italian (`it`) | — | Not started | — |
| Portuguese (`pt`) | — | Not started | — |
| Japanese (`ja`) | — | Not started, needs the optional analyzer | — |
| Korean (`ko`) | — | Not started, needs the optional analyzer | — |
| Chinese (`zh`) | — | Not started, needs the optional analyzer | — |
| Hindi (`hi`) | — | Not started | — |

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
  a simple operator token when enabled. Statistics are shaped for an external health dashboard to
  read; the dashboard belongs to the host.
- Translation is an asynchronous per-clip enrichment in a caller-chosen target language. It never
  blocks source playback or corpus indexing and performs at most one LLM generation call for a cache
  miss.
- `@spoken-usage-retrieval/react` exports a player, typed client, and styles. The host owns the
  modal, navigation, and persistence decisions. Optional target-language and alignment props exist
  from the first version and render source-only when absent.
- Every optional capability degrades honestly: missing model, missing audio, missing key, and missing
  alignment each have a documented visible state, never a silently fabricated value.
- Human labels are the only headline evaluation evidence. Judge and distant-supervision labels carry
  explicit provenance and never enter the held-out test set.
- Credentials remain local and untracked. Other artifacts are untracked by default, but may be
  published deliberately when licensing, size, and reproducibility make that reasonable.

## Original-request coverage

| Requirement | Plans |
| --- | --- |
| Public GitHub quality, license, CI, documentation, packaging | 01, 15 |
| Dedicated channel directory and popular-language catalogues | 02, catalogue template |
| Language-neutral source handling and query-selected translation language | 02, 05, 08, 06 |
| Singular/plural, gender, conjugation, and base-form retrieval in both directions | 03, 04 |
| Candidate limits, random samples, quality ranking, and result diversity | 05, 11–13 |
| Public/hand-labeled data, labeling UI, logistic baseline, small-model fine-tuning | 11–13 |
| Creator-authored subtitles in every available language, excluding auto-translation | 08 |
| Audio download, clarity features, subtitle validation, and ASR fallback | 09, 12 |
| Manual-first and automatic-second forced alignment | 10, 14 |
| Periodic discovery, incremental indexing, pause/resume, health, and statistics | 05, 14 |
| Optional one-call translation, semantic groups, loading, cancellation, and fallback | 08, 06 |
| Reusable Python package, service, CLI, React player, and Acervo integration | 05, 06, 07 |
| Article example provenance, channel display, video modal, and LLM context selection | 07 |
| Cross-service health page in the host application | Tracked in the Acervo repository |

## Where the previous plans went

The roadmap was resequenced so that integration and human labeling happen early rather than last.
Three merges paid for one new experiment plan, and the nine-language catalogue plan became a
template.

| Previous | Now |
| --- | --- |
| 01 Public repository foundations | 01, unchanged |
| 02 Multilingual corpus model | 02, unchanged |
| 03 Channel catalogue expansion | `templates/language-catalogue-session.md` plus the tracker above |
| 04 Language analysis and morphological retrieval | 03, with a lighter default analyzer |
| 05 Authored caption-track acquisition | merged into 08 |
| 06 Audio cache and media preparation | merged into 09 |
| 07 Subtitle reliability benchmark | merged into 09 |
| 08 Audio forced alignment | 10 |
| 09 Incremental background indexing | 14, moved after integration |
| 10 Library, CLI, and service API | 05, no longer waiting on background indexing |
| 11 Query-time translation and semantic alignment | merged into 08 |
| 12 Reusable React player | 06 |
| 13 Evaluation, labeling, and LLM judge | 11, no longer waiting on audio and alignment |
| 14 Interpretable ranking and diversification | 12 |
| 15 Learned multilingual ranker | 13 |
| 16 Acervo integration and release | split into 07 (early integration) and 15 (release) |
| — | 04 Analyzer comparison experiment, new |

Design decisions, the measured baseline, and known constraints live in
[`../design.md`](../design.md), while completed experimental evidence lives in
[`../../experiments/index.md`](../../experiments/index.md). Plans describe future work; design notes
describe why the system is shaped as it is; experiment reports describe observations.

The original working brief remains in `TODO.txt` for now as a source record. It can be retired in a
later documentation cleanup only after the owner explicitly asks for that.
