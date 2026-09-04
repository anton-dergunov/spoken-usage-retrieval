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

## Next experiments

1. Label a small set of retrieved examples for boundary quality and learner usefulness before
   changing the ranking formula.
2. Compare punctuation restoration and audio pause/VAD boundaries against the deterministic
   subtitle-only baseline.
3. Add Spanish lemmatization as a separate retrieval mode and measure false positives rather than
   replacing exact lookup.
4. Use word-level ASR or forced alignment only for top-ranked candidates where timestamp refinement
   has product value.
5. Compare lexical candidates with embedding retrieval, then rerank within the same evaluation set.

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
