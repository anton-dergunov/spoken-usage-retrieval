# MVP feasibility notes

This document records what the first real corpus run reveals. Exact corpus figures are updated after
running acquisition and indexing because YouTube caption availability changes over time.

## What the prototype tests

- Channel discovery and Spanish-caption acquisition without downloading video or audio.
- Manual-caption preference with automatic Spanish captions as a visible fallback.
- Timestamp-aware reconstruction of complete utterances from caption events.
- Exact accent-tolerant word and contiguous 1–5-word phrase retrieval.
- Deterministic baseline ranking, cross-video diversification, and range-limited YouTube playback.

## Known constraints

- Automatic captions often arrive as rolling word events with limited punctuation. Pause and hard
  boundaries are useful baselines, not reliable linguistic sentence boundaries.
- Subtitle timestamps identify caption events, not exact acoustic word onsets. YouTube also seeks to
  nearby keyframes, so the player adds context padding and enforces its end in JavaScript.
- Exact n-grams do not retrieve inflections such as `estar` → `estoy`, discontinuous expressions,
  paraphrases, or speech that captions transcribed incorrectly.
- A video can disable embedding or become unavailable after indexing. The viewer therefore exposes
  a timestamped link back to YouTube.

## Open questions these constraints raise

These are observations about what the baseline cannot yet answer, not a work plan. The
[roadmap](plans/README.md) owns sequencing, and each linked plan carries the method:

- Is lemma retrieval worth its false positives, and which analyzer should each language use
  ([Plan 03](plans/03-morphological-retrieval.md),
  [Plan 04](plans/04-analyzer-comparison-experiment.md))?
- How far do caption cue boundaries diverge from real sentence boundaries and real transcripts
  ([Plan 09](plans/09-audio-and-caption-reliability.md))?
- Does forced alignment beat cue timing enough to be worth its cost, and only for which source
  classes ([Plan 10](plans/10-forced-alignment.md))?
- Do retrieved clips actually help a learner, and can any ranking beat the deterministic baseline
  ([Plan 11](plans/11-evaluation-labeling-and-llm-judge.md),
  [Plan 12](plans/12-ranking-features-and-diversification.md),
  [Plan 13](plans/13-learned-multilingual-reranker.md))?
- Would embedding retrieval add candidates that exact and lemma lookup miss? Not yet planned; it
  needs the evaluation set to be measurable at all.

## Seed run

The first complete run on 4 September 2026 acquired all ten requested videos without a failed
candidate. Round-robin selection produced three Easy Spanish videos, three Spanish After Hours
videos, two Luisito Comunica videos, and two LUZU TV videos. Three tracks were manually authored;
seven were original-language automatic captions.

The rebuilt index contains:

- 10 videos and 3,264 searchable utterances;
- 102,898 stored 1–5-gram occurrences;
- 2,950 punctuation boundaries, 238 pause boundaries, 75 forced boundaries, and one end-of-track
  boundary;
- 30 MB of local generated data, of which the SQLite prototype index is about 27 MB.

This is a positive baseline for the chosen sources: about 90% of segments ended at caption
punctuation, while only about 2.3% needed the 15-second/32-token hard limit. That figure measures
caption punctuation availability rather than true acoustic sentence accuracy and should not be
treated as a quality label.

Representative exact retrieval counts were 60 occurrences for `verdad`, 110 for `entonces`, 42 for
`la verdad`, and 25 for `por ejemplo`. The leading results covered multiple videos and produced
coherent short contexts such as “No, la verdad es que sí, pues son como cinco veces más” and “Por
ejemplo, mira, te enseño.”

The generated `data/reports/acquisition.json` and `data/reports/index-build.json` files remain the
source of truth for the current local corpus.

The production interface was also rendered against the real index at 1440-pixel desktop and
500-pixel compact widths. The master-detail view remained readable at desktop size and changed to a
player-first stacked layout at the compact breakpoint without horizontal overflow.
