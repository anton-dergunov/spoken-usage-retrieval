# Native Speech Retrieval for Language Learning

Find **real examples of words and phrases in native speech**, and play the exact video moment where
each one is spoken.

The project builds a searchable corpus from curated YouTube channels, retrieves occurrences of a
target word or phrase, ranks the most useful examples, and returns the precise segments where the
expression is used. It is both a reusable component for a language-learning application and an
experimental platform for information-retrieval work on phrase matching, speech–text alignment, and
example-quality ranking.

![Web demo showing Spanish-speech search results](docs/assets/web-demo.webp)

*Web demo: search results from native Spanish speech with the matching video excerpt.*

## Current state

A working Spanish subtitle-only baseline. No video or audio is downloaded.

- channel discovery and caption acquisition via `yt-dlp`, with creator-authored captions preferred
  and original-language automatic captions as a visible fallback;
- timestamp-aware reconstruction of complete utterances from caption events;
- exact, accent-tolerant retrieval of words and contiguous 1–5-word phrases;
- deterministic ranking, cross-video diversification, and a JSON API;
- a React viewer that plays only the relevant excerpt and renders progressive subtitles itself.

Not yet: other languages, inflected-form search, translation, forced alignment, and any learned
ranking. Those are the [roadmap](docs/plans/README.md).

## Quick start

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

The acquisition limit counts usable cached transcripts, not attempted videos, and repeat runs reuse
valid cache entries. Pass `--channels`, `--data-dir`, `--scan-limit`, or `--max-ngram` to experiment
without changing defaults.

For frontend development, serve the API once with a production build present, or construct it from
`speech_retrieval.api.create_app`, then run `npm --prefix web run dev`; Vite proxies `/api` to port
8000.

### Tests

```bash
uv run pytest
npm --prefix web run test
npm --prefix web run build
```

## How it works

```text
channel catalogue → discovery → caption cache → segmentation → index → retrieval → ranking → viewer
```

Acquired captions are immutable; segmentation, indexes, and everything downstream are rebuildable.
Acquisition, corpus representation, retrieval, ranking, and presentation stay separate, so the
pipeline is usable as a library and service without the demo.

Segments are complete utterances rather than caption cues: caption events are accumulated and closed
at terminal punctuation, a pause, or a hard duration/token limit, and the reason is kept because a
segment that ended at punctuation is better evidence than one that hit the limit.

The reasoning behind these choices, the measured baseline from the first real corpus run, the known
constraints, and the open research questions are in [`docs/design.md`](docs/design.md).

## Corpus configuration

The executable Spanish MVP subset is [`config/mvp_channels.json`](config/mvp_channels.json); the
broader curated catalogue is `spanish_youtube_channels.json`. The
[multilingual corpus plan](docs/plans/02-multilingual-corpus-model.md) consolidates them under
`config/channels/`, keeping explicit enablement, speech-style, regional-variety, and descriptive
metadata.

Source selection optimizes linguistic diversity rather than volume: street interviews, unscripted
conversation, podcasts, travel, documentary, educational material, scripted comedy, and journalism,
across several regional varieties.

## Documentation

- [`docs/design.md`](docs/design.md) — architecture, design decisions, measured baseline, known
  constraints, open questions.
- [`docs/plans/README.md`](docs/plans/README.md) — the ordered roadmap. Foundations and multilingual
  retrieval first, then an integration-ready service and reusable player, then viewer quality and
  retrieval science in parallel, then scaling and release. Integration into the host application
  deliberately comes before the translation, audio, alignment, and ranking research, so that later
  work improves a system already in real use.
- [`experiments/index.md`](experiments/index.md) — dated, reproducible experiments and their
  findings, including negative results.

Generated corpus data is intentionally untracked; the reports under `data/reports/` make a local run
inspectable. Optional models, audio, and acquired inputs are never prerequisites for exact retrieval,
while licensed fixtures, labels, reports, datasets, and model artifacts may be published deliberately
when that improves reproducibility.
