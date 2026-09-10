# Hosting the service

For an application that wants to run this project beside itself and query it — the Acervo
integration of [Plan 07](plans/07-acervo-integration.md) is the first of these, and this document is
what it was written against.

The package never daemonizes. Process supervision, restart policy, persistent storage, the URL and
any credentials are the host's, and so is the container: this repository publishes artifacts, and a
host runs them.

## What a release gives you

Every `v*` tag publishes, from CI:

| Artifact | For |
| --- | --- |
| `spoken_usage_retrieval-<version>-py3-none-any.whl` | the service and its CLI |
| `spoken_usage_retrieval-<version>.tar.gz` | the source distribution |
| `spoken-usage-retrieval-react-<version>.tgz` | `@spoken-usage-retrieval/react`, the player and typed client |
| `SHA256SUMS` | so a host can verify what it downloaded |

The Python and npm versions are the same number and a test holds them to it, so a host pins one
version for both. `docs/openapi-v1.json` in the tagged tree is the contract those artifacts speak.

## Running it

```bash
pip install spoken_usage_retrieval-<version>-py3-none-any.whl
speech-retrieval serve
```

Nothing else is required at build time. In particular:

- **No ffmpeg.** It is needed only by the optional audio path, which is off by default
  (`SPEECH_RETRIEVAL_WITH_AUDIO=false`). The subtitle-only path fetches captions through `yt-dlp`,
  which is an ordinary Python dependency and needs no system binary.
- **No model download.** `analyzer=auto` prefers a locally provisioned Stanza pipeline, falls back
  to `simplemma`, and then to Unicode analysis. It never fetches a model implicitly; only
  `speech-retrieval models download` does that.

### Configuration

Every setting is `SPEECH_RETRIEVAL_` plus the field name. The ones a container must set:

| Variable | Why |
| --- | --- |
| `SPEECH_RETRIEVAL_DATA_DIR` | The default is the **relative** `data`, which is rarely what a container wants. |
| `SPEECH_RETRIEVAL_CATALOGUE_DIR` | The default is the **relative** `config/channels`, which exists only in a source checkout. |
| `SPEECH_RETRIEVAL_HOST` | `0.0.0.0` to be reachable from another container. |
| `SPEECH_RETRIEVAL_ENABLE_CHANNEL_MUTATIONS` | Only if the host offers channel management. |
| `SPEECH_RETRIEVAL_OPERATOR_TOKEN` | **Required** with the two above: binding a non-loopback host with mutations enabled and no token makes `create_app` refuse to start. |

### Storage, and which half is precious

`data_dir` holds two very different things, and a host that puts them on one volume will eventually
lose the expensive one:

```
<data_dir>/raw/          acquired captions — bandwidth, and not politely re-fetchable
<data_dir>/index/        the search index — rebuilt from raw/ by `reindex`
<data_dir>/derived/      segments, translations, alignments — rebuildable, except translations
<data_dir>/reports/      what the last run did, plus the operation lock
```

**Mount `raw/` separately and never delete it.** Everything else can be thrown away and rebuilt from
it at the cost of CPU. Note that `derived/translations.sqlite3` is the one derived artifact that is
*not* free to lose — it is versioned by cache key rather than rebuilt, and reindexing never evicts
it.

### The catalogue directory must be writable, and seeded

Channel management rewrites `<catalogue_dir>/<language>.json` in place, so the mount cannot be
read-only.

It also cannot start empty. `ChannelRepository` only edits a `<language>.json` that already exists,
and the schema rejects a catalogue with no sections, so **an empty catalogue directory cannot be
filled through the API**. The wheel carries the default catalogues for this reason, and a container
should seed on every start:

```bash
speech-retrieval channels seed --into "$SPEECH_RETRIEVAL_CATALOGUE_DIR"
```

It never overwrites, so running it unconditionally is safe: a channel the operator added survives,
while a language a later version introduced is picked up. Adding a channel for a language the seed
does not cover is not currently possible through the API.

### Health

Two endpoints, and the difference matters when a host wires up a container healthcheck:

- `GET /api/v1/health/live` — 200 whenever the process is serving. **This is the healthcheck.**
- `GET /api/v1/health/ready` — 200 only once an index exists and its recorded analyzer resolves.
  Before the first `update --once` it answers 503 `not_ready`, which is honest and correct.

A container healthcheck pointed at readiness will report a brand-new deployment as failed, because
a service with no corpus yet is working exactly as intended. Show readiness in the application
instead; `GET /api/v1/status` degrades gracefully and carries counts, `built_at`, indexed languages
and the analyzer, which is what a status pane actually wants.

### Keeping the corpus fresh

```bash
speech-retrieval update --once
```

A scheduled invocation is all this milestone needs; there is no background worker. It is safe to run
against the same data directory as a live `serve`: it builds into a temporary file and swaps it in
with an atomic rename, and readers open a fresh read-only connection per query, so a request that
straddles the swap still sees one consistent snapshot. A PID lock in `reports/` serializes it
against another `update` or `reindex`.

Cached captions are not re-downloaded. The channel scan itself always makes network calls.

**Run it from the same image that serves.** The index records which analyzer built it and readiness
refuses an index built by a different analyzer version, so one image that both builds and serves
cannot drift while two can. `docker compose exec <service> speech-retrieval update --once` is the
straightforward way to guarantee that.

## Querying it

`GET /api/v1/search?language=<bcp47>&q=<query>` with optional `match_mode` (`auto`, `exact`,
`lemma`), `order` (`ranked`, `random`), `limit` (1–50) and `seed`.

`auto` unions surface and contiguous lemma matches, so a dictionary form retrieves inflections —
which means the right thing to send is the **lemma**, not a surface form.

Each result carries a stable `segment_id`, the sentence, the matched surface with code-point offsets
into that sentence, clip and sentence timing, the video and channel, caption provenance, and rank
and score. A host that stores a selection should store `segment_id`: it is content-derived and
stable, and `GET /api/v1/clips/{segment_id}` returns current timing for playback.

Bound the candidate set. Ranking is deterministic, and asking for far more than you will use costs
the caller nothing here but usually makes whatever consumes the results worse.

## Translation, if you want it

`GET /api/v1/clips/{segment_id}` never populates `target_text`. Translation is a job:
`POST /api/v1/clips/{segment_id}/translations` returns 202 with a `TranslationJob`, already
`complete` when both stages are cached. It produces a target sentence *and* a validated word
alignment graph that `SpeechClipPlayer` renders interactively.

Without a configured provider the service still starts and still serves everything else. A clip
whose video has creator-authored captions in the target language gets that human text verbatim; one
that does not gets a job in state `unavailable`. `GET /api/v1/status` advertises which of these you
are in under `translation.provider_available`.

To use a provider other than Gemini, do not fork this: `TranslationProvider` and
`WordAlignmentProvider` are Protocols and `create_app(settings, translation_provider=…,
alignment_provider=…)` takes them, so a host serves the app itself with its own adapter injected. If
that adapter routes over several models, report a **stable** identity from its `provider` and
`model` attributes — they are inputs to the translation cache key, and reporting whichever model
happened to answer would thrash it. The two stages are cached independently and need not use the
same model.
