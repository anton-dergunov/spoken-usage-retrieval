# Design

Why this system is built the way it is, what the current implementation actually does, and where the
open questions are. The [roadmap](plans/README.md) owns what happens next; the
[experiment index](../experiments/index.md) owns measured findings. This document owns the reasoning
in between.

## The problem

Dictionaries and generated examples are useful, but they do not answer a practical question:

> How do native speakers actually use this word or phrase in real situations?

Given a query such as `dar bronca`, `apurado`, or `estar podrido de algo`, the system should return
several authentic examples from native speech, each with the utterance, an accurate timestamp, enough
surrounding context, the source video and channel, and an optional translation.

Finding textual matches is the easy half. The research problem is identifying the **best short,
self-contained examples** of an expression inside a large collection of noisy real-world video —
which means the interesting work is ranking, not lookup.

## Two purposes, one codebase

**Product.** A library and service integrated into a vocabulary-learning application. A user searches
for a word or phrase and receives ranked examples from real native speech, each linked to the precise
portion of the source video.

**Research.** A compact experimental environment for information-retrieval and ML work: phrase
retrieval, transcript normalization, lexical and semantic matching, utterance segmentation, timestamp
refinement, speech–text alignment, example-quality estimation, diversity-aware ranking, and
model-based reranking.

These pull in the same direction more often than not, but where they conflict the product wins on
defaults and the research wins on optionality: every model-based capability is an extra that the
baseline runs without.

## Layer separation

The organizing principle is that **acquisition, corpus representation, retrieval, ranking, and
presentation stay separate**.

```text
YouTube channel catalogue
        │
        ▼
channel / video discovery ────────► acquisition
        │                            (immutable inputs)
        ▼
caption cache ──────────► optional audio cache
        │
        ▼
normalization + segmentation ─────► corpus representation
        │                            (derived, rebuildable)
        ▼
search index
        │
        ▼
candidate retrieval ──────────────► retrieval
        │
        ▼
example ranking ──────────────────► ranking
        │
        ▼
Python API / CLI / HTTP / player ─► presentation
```

Two rules follow from it, and both have already paid for themselves:

- **Acquired data is immutable; everything derived is rebuildable.** Raw captions and audio are
  fetched once and never rewritten. Segmentation, indexes, alignments, features, and translations can
  all be regenerated, which is what makes an experiment cheap to re-run.
- **Presentation is replaceable.** The pipeline is usable as a library and a service without the web
  demo, so the demo can be a genuine inspection tool rather than the product.

## What exists today

A multilingual subtitle-only architecture, verified end to end with the initial Spanish corpus:

- channel discovery and caption acquisition through `yt-dlp`, with no video or audio download;
- creator-authored captions preferred, original-language automatic captions as a visible fallback;
- timestamp-aware reconstruction of complete utterances from caption events;
- exact, accent-tolerant retrieval of words and contiguous 1–5-word phrases;
- deterministic ranking, cross-video diversification, and range-limited YouTube playback with
  self-rendered progressive subtitles.

### Corpus representation

The indexed unit is a **segment**: one reconstructed utterance with its timing, its per-word timed
sub-segments where the caption source provides them, character offsets back into the original text,
a stable content-derived ID, and the provenance of the track it came from. Occurrences of every
1–5-gram inside a segment are stored separately with their token and character spans, so a match can
be highlighted in the original text without re-tokenizing at query time.

Metadata travels with the segment rather than being inferred later: source language, stable
video/track identity, channel identity, regional varieties, speech style, caption kind and track
language, analyzer identity, and the boundary reason that produced the segment. Later work adds
alignment coverage and ranking features to the same row.

### Cache layout

```text
data/
├── raw/corpora/<language>/<video-key>/<track-id>/
│   ├── metadata.json            # immutable acquired input and provenance
│   └── subtitles.raw.json3      # unchanged source-caption strings
├── index/corpus.sqlite3         # derived, rebuildable
├── derived/corpora/<language>/  # language-partitioned derived debug dumps
└── reports/                     # what a local run actually did
```

Generated corpus data is deliberately untracked. The reports under `data/reports/` are what make a
local run inspectable and are the source of truth for the current corpus.

The cache and database carry explicit schema versions. Pre-version prototype data is deliberately
rebuilt rather than supported through compatibility readers. Acquired captions and media retain
their source licenses and stay local by default. A deliberately published fixture,
dataset, report, label set, or model artifact must record its source, license, and reproducibility
purpose alongside it.

### Segmentation

Subtitle cues are poor linguistic units, so segments are built by accumulating caption events and
closing a group at the first of: terminal punctuation, a pause above a threshold, a hard duration or
token limit, or end of track. Very short groups are merged forward. The boundary reason is retained,
because a segment that ended at punctuation is better evidence than one that hit the hard limit, and
ranking should be able to tell the difference.

The intended output is a clip that starts shortly before the relevant utterance and ends when the
sentence or conversational turn is complete, which is why clips carry a small amount of padding on
each side.

Better boundaries are a progression, not a single fix: subtitle timestamps, then punctuation
restoration, then pause inference, then audio VAD, then ASR word timestamps, then forced alignment.
The baseline deliberately stops at the first two.

### Retrieval

Stage one is exact normalized token matching over an inverted index of n-grams — Unicode
normalization, case folding, diacritic folding with an accent-exactness bonus retained for ranking,
and preserved offsets back into the original text.

Stage two is the interesting one, because real speech does not reproduce a dictionary phrase
verbatim. `estar podrido de` appears as `estoy podrido de…`, `estaba ya podrida de…`,
`estamos completamente podridos de…`. The approaches worth comparing are exact n-grams,
lemma-sequence matching, POS-aware patterns, bounded token gaps, dependency-aware matching, embedding
retrieval, and cross-encoder reranking. Lemma-sequence matching is the next step; the rest stay
research.

### Ranking

Finding an occurrence is candidate generation. Deciding which occurrence to show is the actual
product question.

A useful example tends to have a complete self-contained sentence, clear audio, one dominant speaker,
little background noise, enough surrounding context, high subtitle confidence, natural and
representative use of the target expression, moderate length, few obscure named entities, and a good
match to the learner's intended dialect.

The current implementation combines a few of those signals in a hand-designed deterministic score.
That is a baseline to beat, not a design: the intended shape is

```text
query + candidate segment → usefulness score
```

with the features split into **query-independent segment quality** (is this a good example at all)
and **query-dependent match quality** (is this a good example *of this word*). That split is what
makes precomputation possible and what lets external written-example data inform the first group
without contaminating the second.

Diversity is a first-class part of the result set, not a tie-breaker. Five nearly identical
occurrences from one livestream are much less useful than five from different speakers, channels,
countries, contexts, grammatical forms, or speech styles.

### Translation

Translation is a derived, per-clip feature, never part of the source corpus and never part of an
index key. The target language is chosen at query time. Translations are cached independently and
generated lazily only for clips someone actually opens, so a missing provider degrades to
source-language playback rather than to failure.

### Optional audio

Audio is not required for lexical search, but it enables refined word timestamps, voice activity
detection, boundary estimation, acoustic quality scoring, overlap detection, ASR verification, and
forced alignment. It is therefore an optional cache layer with its own opt-in, not a dependency of
retrieval.

## Corpus selection

Source selection optimizes linguistic diversity rather than volume. Each BCP-47 source language has
one versioned catalogue with explicit activation. The Spanish starter corpus mixes street
interviews, unscripted conversation, podcasts and streaming, travel, documentary, educational
material, scripted comedy, and journalism, across several regional varieties.

Channel entries are editorial content: each records its varieties, speech styles, and a concrete
description of retrieval value and limitations, so the corpus can be audited by reading the catalogue.

## Measured baseline

From the first complete run, on 4 September 2026, over four Spanish channels:

- all ten requested videos acquired with no failed candidate; round-robin selection produced three
  Easy Spanish, three Spanish After Hours, two Luisito Comunica, and two LUZU TV videos;
- three manually authored tracks and seven original-language automatic tracks;
- 3,264 searchable utterances and 102,898 stored 1–5-gram occurrences;
- 2,950 punctuation boundaries, 238 pause boundaries, 75 forced boundaries, one end-of-track
  boundary;
- about 30 MB of generated data, of which the SQLite index is about 27 MB.

About 90% of segments ended at caption punctuation and only about 2.3% needed the hard limit. That
is a positive result for these sources, but it measures **caption punctuation availability**, not
true sentence accuracy, and must not be read as a quality label.

Representative exact retrieval counts were 60 occurrences for `verdad`, 110 for `entonces`, 42 for
`la verdad`, and 25 for `por ejemplo`. Leading results spanned multiple videos and produced coherent
short contexts such as “No, la verdad es que sí, pues son como cinco veces más” and “Por ejemplo,
mira, te enseño.”

The interface was rendered against this index at 1440-pixel and 500-pixel widths; the master-detail
view stayed readable and switched to a player-first stacked layout at the compact breakpoint without
horizontal overflow.

## Known constraints

- Automatic captions often arrive as rolling word events with limited punctuation. Pause and hard
  boundaries are useful baselines, not reliable linguistic sentence boundaries.
- Subtitle timestamps identify caption events, not acoustic word onsets. YouTube also seeks to nearby
  keyframes, so the player pads clips and enforces the end in JavaScript.
- Exact n-grams do not retrieve inflections such as `estar` → `estoy`, discontinuous expressions,
  paraphrases, or speech the captions transcribed incorrectly.
- The n-gram index is stored in full and rebuilt in full, which is fine at ten videos and will not be
  fine at a thousand.
- A video can disable embedding or disappear after indexing, so the viewer always exposes a
  timestamped link back to the source.

## Open questions

What the baseline cannot yet answer. Each linked plan carries the method:

- Is lemma retrieval worth its false positives, and which analyzer should each language use
  ([Plan 03](plans/03-morphological-retrieval.md),
  [Plan 04](plans/04-analyzer-comparison-experiment.md))?
- How far do caption cue boundaries and caption text diverge from real speech
  ([Plan 09](plans/09-audio-and-caption-reliability.md))?
- Does forced alignment beat cue timing enough to justify its cost, and for which source classes
  ([Plan 10](plans/10-forced-alignment.md))?
- Do retrieved clips actually help a learner, and can any ranker beat the deterministic baseline
  ([Plan 11](plans/11-evaluation-labeling-and-llm-judge.md),
  [Plan 12](plans/12-ranking-features-and-diversification.md),
  [Plan 13](plans/13-learned-multilingual-reranker.md))?
- Would embedding retrieval add candidates that exact and lemma lookup miss? Not planned yet; it
  needs the evaluation set before it can be measured at all.

The long-term version of the same question:

> Given a vocabulary item and hundreds of authentic occurrences, can a model rank the examples in
> approximately the same order as an experienced language teacher?

## Evaluation stance

Because this is partly an IR research project, evaluation is a first-class part of the repository
rather than an afterthought. A small human-labeled benchmark — even 30–50 carefully chosen words and
phrases with judgments — makes experiments meaningfully comparable, and metrics stay conventional:
Recall@K for candidate generation, MRR and NDCG for ranking, pairwise preference accuracy, phrase
recall, boundary error, and diversity.

Two commitments hold across every ranking experiment: human labels are the only headline evidence,
and a deterministic baseline plus a seeded random control are always reported alongside any model.
