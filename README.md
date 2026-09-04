# Native Speech Retrieval for Language Learning

A research-oriented information retrieval system for finding **real examples of words and phrases in native Spanish speech**.

The project builds a searchable corpus from selected YouTube channels, retrieves occurrences of a target word or phrase, ranks the most useful examples, and returns the exact video segments where the expression is spoken. It is designed both as a reusable library for a language-learning application and as an experimental platform for work on retrieval, phrase matching, speech–text alignment, and example-quality ranking.

## MVP quick start

The repository contains a subtitle-only acquisition pipeline, deterministic 1–5-gram index, JSON
API, and custom React YouTube excerpt viewer. Video and audio files are never downloaded.

Requirements: Python 3.12+, Node.js 20+, and network access for the acquisition step.

```bash
uv sync --extra test
npm install --prefix web

# Cache ten successfully captioned videos across the four MVP channels.
uv run python scripts/download_subtitles.py --limit 10

# Reconstruct utterances and build data/index/corpus.sqlite3.
uv run python scripts/build_index.py --max-ngram 5

# Build and serve the interface at http://127.0.0.1:8000.
npm --prefix web run build
uv run python scripts/serve.py --port 8000
```

The acquisition limit counts usable cached transcripts, not attempted videos. Repeat runs reuse
valid cache entries. To experiment elsewhere without changing defaults, pass `--channels`,
`--data-dir`, `--scan-limit`, or `--max-ngram` to the relevant script.

For frontend development, run the API with the production build once, or construct it from
`speech_retrieval.api.create_app`, then run `npm --prefix web run dev`; Vite proxies `/api` to port
8000.

### Tests

```bash
uv run pytest
npm --prefix web run test
npm --prefix web run build
```

Generated corpus data is intentionally ignored by Git. Acquisition and index reports under
`data/reports/` make a local run inspectable. See [`docs/feasibility-notes.md`](docs/feasibility-notes.md)
for findings and limitations from the seeded prototype.

## Motivation

Dictionaries and generated examples are useful, but they often fail to answer a more practical question:

> *How do native speakers actually use this word or phrase in real situations?*

Given a query such as `dar bronca`, `apurado`, or `estar podrido de algo`, the system should retrieve several authentic examples from native Spanish videos, together with:

- the relevant sentence or utterance,
- an accurate timestamp,
- the surrounding context,
- the source video and channel,
- an optional translation,
- and eventually a score estimating how useful the example is for a language learner.

The goal is not merely to find textual matches. The longer-term research problem is to identify the **best short, self-contained examples** of a linguistic expression inside a large collection of noisy real-world video.

## Project goals

The project has two complementary purposes.

### Product

Provide a library and service that can be integrated into a vocabulary-learning application. A user searches for a word or phrase and receives ranked examples from real native speech, with each result linked to the precise portion of the source video.

### Research

Provide a compact experimental environment for information-retrieval and ML work around:

- word and phrase retrieval,
- transcript normalization,
- lexical and semantic matching,
- sentence and utterance segmentation,
- timestamp refinement,
- speech–text alignment,
- example quality estimation,
- diversity-aware ranking,
- dialect and register-aware retrieval,
- and learning-to-rank or model-based reranking.

The project is intentionally structured so that simple baselines can be implemented first and progressively replaced by stronger methods.

## High-level workflow

```text
YouTube channel list
        │
        ▼
Channel / video discovery
        │
        ▼
Subtitle acquisition
        │
        ▼
Local transcript cache
        │
        ├──────────────► optional audio cache
        │
        ▼
Normalization + segmentation
        │
        ▼
Search index
        │
        ▼
Candidate retrieval
        │
        ▼
Segment / example ranking
        │
        ▼
CLI / Python API / web demo
```

## Initial scope

The first version can remain deliberately simple:

1. Read a configured list of YouTube channels.
2. Discover videos from those channels.
3. Download available Spanish subtitles and cache them locally.
4. Periodically check for newly published videos.
5. Normalize and segment transcripts.
6. Build an inverted index for exact word lookup.
7. Search the corpus by a single word.
8. Return matching sentences with video IDs and timestamps.
9. Rank results using simple deterministic signals.
10. Expose the same functionality through a small web demo.

This establishes the full end-to-end pipeline before introducing more experimental components.

## Example query

```bash
speech-retrieval search "bronca"
```

Example output:

```text
1. "A mí me da mucha bronca cuando pasa eso."
   Channel: ...
   Video: ...
   Time: 01:17:43–01:17:49
   Variety: es-AR
   Score: 0.91

2. "No me dio bronca, pero sí me sorprendió."
   Channel: ...
   Video: ...
   Time: 00:23:18–00:23:24
   Variety: es-AR
   Score: 0.84
```

A phrase query could later behave similarly:

```bash
speech-retrieval search "estar podrido de"
```

## Data model

A transcript cache should preserve the raw source data while also exposing normalized representations for retrieval.

A possible unit of indexed data is:

```json
{
  "video_id": "abc123",
  "channel_id": "channel-id",
  "language": "es",
  "locale": "es-AR",
  "start": 4663.2,
  "end": 4669.4,
  "text": "A mí me da mucha bronca cuando pasa eso.",
  "normalized_text": "a mi me da mucha bronca cuando pasa eso",
  "source": "youtube"
}
```

Useful metadata may later include:

- country / regional variety,
- speaker identity when known,
- number of speakers,
- speech style,
- formality,
- subtitle source,
- automatic vs manually authored captions,
- audio quality,
- confidence scores,
- sentence-boundary confidence,
- and embedding vectors or retrieval features.

## Local cache

The acquisition layer should be separated from indexing so that experiments can be repeated without repeatedly accessing the original source.

One possible layout:

```text
data/
├── channels.json
├── videos/
│   └── <video_id>/
│       ├── metadata.json
│       ├── subtitles.raw.json
│       ├── transcript.json
│       └── audio.*              # optional
└── index/
```

The raw transcript should be treated as immutable source data. Derived segmentation, alignment, features, and indexes can then be regenerated independently.

## Periodic updates

The application is intended to run as a small background service.

On each update cycle it should:

- reload the channel configuration,
- detect channels added or removed from the configuration,
- discover newly published videos,
- check whether previously known videos remain available,
- acquire subtitles for unseen videos,
- regenerate only the affected derived data,
- and update the search index incrementally where practical.

The channel file should therefore be considered live configuration rather than only bootstrap input.

## Retrieval

### Stage 1 — exact lexical retrieval

The first baseline can use normalized token matching and an inverted index.

Important details include:

- Unicode normalization,
- case folding,
- punctuation handling,
- Spanish diacritics,
- token boundaries,
- optional lemmatization,
- and preserving offsets back into the original subtitle text.

For a first implementation, exact token matching is enough.

### Stage 2 — phrase retrieval

Phrase retrieval becomes considerably more interesting because real speech does not always reproduce a dictionary phrase verbatim.

A query such as:

```text
estar podrido de
```

may appear as:

```text
estoy podrido de...
estaba ya podrida de...
estamos completamente podridos de...
```

Useful approaches to compare include:

- exact n-gram matching,
- lemma-sequence matching,
- POS-aware patterns,
- bounded token gaps,
- dependency-aware matching,
- embedding retrieval,
- cross-encoder reranking,
- and LLM-assisted candidate interpretation.

This makes phrase retrieval one of the main research components of the project.

## Segment boundaries

Subtitle chunks are often poor linguistic units. A useful result should correspond to an intelligible sentence or short utterance rather than an arbitrary caption boundary.

The system can progressively improve segmentation using:

1. subtitle timestamps,
2. punctuation restoration,
3. sentence segmentation,
4. pauses inferred from subtitle timing,
5. audio silence / VAD,
6. ASR word timestamps,
7. forced alignment.

The target output is a clip that begins shortly before the relevant utterance and ends when the sentence or conversational turn is complete.

## Optional audio processing

Audio downloading is not required for basic lexical search, but it can enable substantially better experiments.

Possible uses include:

- refined word-level timestamps,
- voice activity detection,
- sentence-boundary estimation,
- acoustic quality scoring,
- overlap / multiple-speaker detection,
- ASR verification,
- subtitle correction,
- and forced alignment.

Audio should therefore be an optional cache layer rather than a hard dependency of the initial retrieval pipeline.

## Ranking useful examples

Finding an occurrence is only candidate generation. The more interesting problem is deciding which occurrence is most valuable to show.

A useful example might have:

- a complete, self-contained sentence,
- clear audio,
- one dominant speaker,
- little background noise,
- enough surrounding context,
- high subtitle confidence,
- a natural and representative use of the target expression,
- moderate sentence length,
- minimal named entities or obscure references,
- and a good match to the learner's intended dialect.

An initial hand-designed score could combine these signals.

Later versions can treat this as a ranking problem:

```text
query + candidate segment → usefulness score
```

Potential approaches include:

- manually designed ranking features,
- pairwise preference labels,
- learning-to-rank,
- embedding-based relevance,
- cross-encoder reranking,
- LLM-as-judge experiments,
- or a small trained quality model.

The project can maintain an evaluation set of queries with human-ranked candidate examples to make these experiments reproducible.

## Diversity-aware ranking

Returning five nearly identical examples from the same podcast episode is not very useful.

Reranking can therefore optimize not only relevance but also diversity across:

- speakers,
- channels,
- countries,
- dialects,
- contexts,
- grammatical forms,
- and speech styles.

For example, a search for `bronca` might deliberately surface several Argentine examples from different settings instead of five adjacent occurrences from one long livestream.

## Translation

Translation is a derived feature rather than part of the source corpus.

For each selected sentence the system may generate:

- a literal translation,
- a natural translation,
- and optionally a brief explanation of the expression in context.

Translations can be cached independently and generated lazily only for retrieved examples.

## Web demo

The project should include a minimal browser-based demo for inspecting retrieval quality.

A search result can show:

- the original Spanish sentence,
- the queried word or phrase highlighted,
- its translation,
- channel and video metadata,
- dialect / country metadata,
- ranking signals,
- and an embedded YouTube player.

Selecting a result should seek directly to the relevant timestamp and play only the local context window.

A later version can synchronize transcript highlighting with playback at the word level.

```text
┌─────────────────────────────────────────────────────┐
│ Search:  dar bronca                            [→]  │
├─────────────────────────────────────────────────────┤
│ 1. A mí me DA BRONCA cuando hacen eso.              │
│    It really annoys me when they do that.            │
│    🇦🇷 casual conversation · 01:17:43                │
│                                                     │
│             [ embedded YouTube player ]             │
│                                                     │
│                 ▶ Play example                      │
├─────────────────────────────────────────────────────┤
│ 2. ...                                               │
└─────────────────────────────────────────────────────┘
```

The web interface is primarily an inspection and demonstration tool; the retrieval pipeline should remain usable independently as a Python library and command-line tool.

## Interfaces

The project can expose three layers.

### Python

```python
from speech_retrieval import Corpus

corpus = Corpus("data")
results = corpus.search("bronca", limit=10)
```

### CLI

```bash
speech-retrieval update
speech-retrieval build-index
speech-retrieval search "dar bronca"
speech-retrieval serve
```

### HTTP API

```text
GET /search?q=dar+bronca
GET /videos/{video_id}
GET /segments/{segment_id}
```

## Evaluation

Because this is also an IR research project, evaluation should be a first-class part of the repository.

A small benchmark can contain:

```text
query
candidate segment
relevance
example quality
boundary quality
audio quality
dialect
notes
```

Metrics might include:

- Recall@K for candidate generation,
- MRR / NDCG for ranking,
- pairwise preference accuracy,
- phrase retrieval recall,
- timestamp / boundary error,
- and diversity metrics.

The evaluation set can remain small initially. Even 30–50 carefully selected words and phrases with human judgments would make experiments considerably more meaningful than purely qualitative inspection.

## Research directions

Once the baseline system works, the repository can support experiments such as:

- exact matching vs morphological matching,
- lexical vs dense phrase retrieval,
- query expansion from dictionary forms,
- contextual phrase detection,
- hybrid BM25 + embedding retrieval,
- sentence quality prediction,
- subtitle reliability estimation,
- forced alignment,
- multilingual translation models,
- dialect classification,
- diversity-aware reranking,
- personalized ranking by learner level,
- and automatic extraction of especially illustrative examples.

A particularly interesting long-term problem is:

> Given a vocabulary item and hundreds of authentic occurrences, can a model rank the examples in approximately the same order as an experienced language teacher?

## Corpus configuration

The broad Spanish source catalogue is defined in [`spanish_youtube_channels.json`](spanish_youtube_channels.json).
The executable MVP subset is [`config/mvp_channels.json`](config/mvp_channels.json). Channels are
grouped by speech style and regional value so that experiments can use either the entire collection
or selected subsets.

The initial corpus deliberately mixes:

- street interviews,
- unscripted conversations,
- podcasts and streaming,
- travel,
- documentaries,
- educational material,
- scripted comedy,
- journalism,
- and multiple regional varieties of Spanish.

This is intended to maximize linguistic diversity rather than simply maximize the number of indexed videos.

## Project status

The intended implementation order is:

```text
[x] channel configuration
[x] video discovery
[x] subtitle acquisition + local cache
[x] transcript normalization
[x] sentence segmentation
[x] exact word index
[ ] CLI search
[x] timestamped result rendering
[x] web demo + YouTube playback
[x] exact n-gram phrase retrieval baseline
[x] deterministic example-quality ranking
[ ] audio-assisted alignment
[ ] evaluation benchmark
[ ] learned reranking
[ ] synchronized transcript playback
```

## Design principle

Keep **acquisition**, **corpus representation**, **retrieval**, **ranking**, and **presentation** separate.

That makes the system useful as an application component while preserving the repository as a clean experimental environment for information retrieval and machine-learning research.
