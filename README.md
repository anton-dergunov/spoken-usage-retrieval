# Native Speech Retrieval for Language Learning

[![CI](https://github.com/anton-dergunov/spoken-usage-retrieval/actions/workflows/ci.yml/badge.svg)](https://github.com/anton-dergunov/spoken-usage-retrieval/actions/workflows/ci.yml)

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

A working multilingual subtitle-only architecture with an initial Spanish catalogue. No video or
audio is downloaded.

- versioned per-language channel catalogues and caption acquisition via `yt-dlp`, with
  creator-authored source-language captions preferred and original-language automatic captions as a
  visible fallback;
- timestamp-aware reconstruction of complete utterances from caption events;
- accent-tolerant surface and morphological retrieval of words and contiguous 1–5-word phrases,
  with related word forms enabled by default and exact matches ranked first;
- deterministic ranking, cross-video diversification, and a JSON API;
- a React viewer that plays only the relevant excerpt and renders progressive subtitles itself.

Not yet: curated catalogues beyond Spanish, translation, forced alignment,
and any learned ranking. Those are the [roadmap](docs/plans/README.md).

## Quick start

Requirements: Python 3.12+, Node.js 22+, [uv](https://docs.astral.sh/uv/), npm, and network
access for the acquisition step.

```bash
uv sync --locked
npm ci --prefix web

# Cache ten successfully captioned videos across the four enabled Spanish channels.
uv run speech-retrieval download-subtitles --limit 10

# Reconstruct utterances and build data/index/corpus.sqlite3.
uv run speech-retrieval build-index --max-ngram 5

# Build and serve the interface at http://127.0.0.1:8000.
npm --prefix web run build
uv run speech-retrieval serve --port 8000
```

`download-subtitles` is the only quick-start command that contacts YouTube. Its limit counts usable
cached transcripts, not attempted videos, and repeat runs reuse valid cache entries. It defaults to
`config/channels/es.json`; pass `--channels`, `--data-dir`, `--scan-limit`, or `--max-ngram` to
experiment without changing defaults.
The repository scripts remain available as compatibility wrappers around the same CLI handlers.

For frontend development, serve the API once with a production build present, or construct it from
`speech_retrieval.api.create_app`, then run `npm --prefix web run dev`; Vite proxies `/api` to port
8000.

### Word forms and optional language models

Morphology works immediately after a normal installation for simplemma-supported languages,
including Spanish, English, Portuguese, French, German, Italian, and Hindi. No model download is
needed. The viewer's **Word forms** selector compares the three retrieval modes:

```python
from speech_retrieval.search import Corpus

corpus = Corpus("data")
corpus.search("casas", source_language="es")  # auto: includes casa, surface matches first
corpus.search("casas", source_language="es", match_mode="exact")
corpus.search("casas", source_language="es", match_mode="lemma")
```

HTTP uses `/api/search?language=es&q=casas&match_mode=auto`. `exact` searches the accent-folded
surface inventory, `lemma` searches dictionary-form sequences, and `auto` combines them. Results
retain original text and offsets and add `match_type`, `matched_surface`, `matched_lemma`,
`token_analysis`, and `analyzer`. `query_analyses` reports candidate lemmas by token, their origin,
and observed frequencies; it does not claim to enumerate every possible meaning.
`totals_by_mode` reports exact, lemma, and deduplicated auto occurrence counts before sentence
selection and the result limit. Exact and lemma totals can overlap.

Japanese, Korean, and Chinese need the optional Stanza analyzer and explicitly installed models:

```bash
uv sync --locked --extra nlp
uv run speech-retrieval models download ja
uv run speech-retrieval build-index
```

The default model directory is `<data-dir>/models/stanza`. `models download`, `build-index`, and
`serve` accept `--models-dir`; Python `Corpus` and `create_app` accept `models_dir`. Downloading a
model alone does not change an existing index: rebuild it to enable that analyzer. To compare
Stanza on a language normally handled by simplemma, use `build-index --analyzer stanza`.
`--analyzer unicode` creates an exact-only baseline; `--analyzer simplemma` explicitly selects the
normal dictionary analyzer. Indexing and search never download models.

Without usable morphology, `auto` returns surface matches and `morphology_available: false` with
an explanation. Explicit `lemma` raises `UnsupportedAnalysisError`; HTTP returns 400 with
`detail.code: "unsupported_analysis"` and `detail.message`. Incompatible schema or analysis versions
return a rebuild requirement (HTTP 503). Schema 2 indexes must be rebuilt from versioned caption
caches; raw acquisition files remain unchanged.

Simplemma supplies neither POS nor morphological features, so those fields are nullable. It can
miss inflections or choose an unintended lemma; related forms are candidates, not proof of the same
meaning. See the [measured performance and limitations](experiments/morphological-retrieval/README.md).

### Tests

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run speech-retrieval smoke
npm --prefix web run test
npm --prefix web run build
```

The smoke command builds and queries a temporary synthetic corpus, exercises the JSON API, and uses
no network, API key, model download, or local `data/` directory.
The normal suite mocks Stanza and skips real CJK integration unless the `nlp` extra and local
models are available. Set `SPEECH_RETRIEVAL_TEST_MODELS_DIR` to test models in a custom directory;
then run `uv run pytest tests/test_stanza_analysis.py -rs`. Tests never download weights.

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

The versioned Spanish catalogue is [`config/channels/es.json`](config/channels/es.json). It preserves
all 24 curated sources and marks the four executable MVP channels as enabled. Additional catalogues
use the same schema and a canonical BCP-47 filename under `config/channels/`.

Source selection optimizes linguistic diversity rather than volume: street interviews, unscripted
conversation, podcasts, travel, documentary, educational material, scripted comedy, and journalism,
across several regional varieties.

## Data and licensing

Project-authored code, documentation, and synthetic test fixtures are licensed under the
[MIT License](LICENSE). Captions, videos, audio, datasets, and models obtained from third parties
retain their own copyrights and license terms; the project license does not grant permission to
redistribute them.

Generated corpus data stays local and untracked by default:

```text
data/
├── raw/corpora/<language>/<video-key>/<track-id>/
│                                  # acquired metadata and captions; immutable inputs
├── derived/corpora/<language>/  # rebuildable segments and debug artifacts
├── index/corpus.sqlite3         # rebuildable search index
└── reports/                     # acquisition and index-build reports
```

The multilingual cache and index carry explicit schema versions. Pre-version local data is not read;
rerun acquisition and rebuild the disposable index after upgrading. A small fixture, dataset, label
set, report, or model artifact may be deliberately published when redistribution is permitted, its
size is reasonable, and it materially improves reproducibility. Its source, license, and purpose
must be documented alongside it. Credentials, cookies, tokens, and personal environment files must
never be committed.

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
