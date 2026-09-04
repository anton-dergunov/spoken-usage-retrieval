# Plan 05: Library, CLI, and service API

**Status:** Planned

**Depends on:** Plans 02 and 03

## Outcome

Stabilize one importable Python surface, a foreground command line, and versioned HTTP routes for
retrieval and corpus operation. Local research remains easy, while a host application can supervise
the same service without depending on repository scripts.

This is the first plan a host application can actually consume, so it deliberately does not wait for
the background worker, audio, alignment, translation, or ranking work.

## Current state

- Package modules exist, but setup and operational behavior are still exposed primarily through
  scripts.
- FastAPI serves search and clip data for the demo without a declared versioned public contract.
- There is no channel-management, readiness, or statistics surface.
- Acquisition and index building are whole-run scripts. That is sufficient here: a scheduled
  `update --once` invocation keeps a corpus fresh, and Plan 14 replaces it with incremental work
  later.

## Decisions

- Keep the public Python surface small: `Corpus`, `Indexer`, `ChannelRepository`, settings, result
  types, and `create_app(settings)`. Other modules remain implementation details until reused.
- `speech-retrieval serve` and `speech-retrieval update --once` stay in the foreground. Docker,
  systemd, launchd, or another host owns start, stop, restart, log collection, scheduling, and
  persistent volumes. The package never self-daemonizes.
- `Indexer.update_once()` in this plan is the existing discovery, acquisition and index build over
  enabled channels, wrapped in a structured summary. No job table, retry state machine, or priority
  queue; Plan 14 adds those when corpus size makes full rebuilds impractical.
- Version public HTTP routes under `/api/v1`. Add OpenAPI examples and contract tests before adding
  client conveniences.
- Bind to loopback by default. Mutating channel routes are disabled unless configured and require one
  operator token when exposed beyond a trusted local network. Do not build accounts, roles, sessions,
  or an authentication service.
- Support deterministic `ranked` ordering and reproducible `random` ordering with an optional seed.
  A limit always bounds materialized results, so a very common word returns a usable sample rather
  than hundreds of near-duplicates.
- Statistics are designed for an external dashboard to read: per-language and per-channel video and
  segment counts, caption-provenance histogram, last successful update, current activity, and recent
  failures. The dashboard itself belongs to the host.

## Implementation work

1. Consolidate settings and resource construction so library users, CLI commands, tests, and FastAPI
   share the same paths, catalogue, analyzer, and optional capability configuration.
2. Stabilize `Corpus.search(...)`, clip lookup, suggestions, corpus statistics, and close/context
   management with typed result models.
3. Stabilize `Indexer.update_once()` and channel repository list/add/update/enable/disable operations.
4. Implement CLI commands: `serve`, `update --once`, `search`, `channels
   list|add|update|enable|disable`, `status`, `reindex`, `models download|list`, and `doctor`.
   Commands should be thin adapters and support JSON output where automation benefits.
5. Add `/api/v1/search`, `/api/v1/clips/{segment_id}`, channel CRUD/activation, statistics, status,
   and liveness/readiness endpoints. Reserve the worker-control route names for Plan 14 rather than
   implementing them now.
6. Add consistent validation/error bodies, request IDs, modest request limits, and graceful resource
   shutdown. Keep observability to useful structured logs and status fields.
7. Generate and snapshot the OpenAPI document and add contract tests used by the React client in
   Plan 06.

## Public interfaces and data

```python
Corpus.search(
    query,
    source_language,
    match_mode="auto",
    order="ranked",
    limit=20,
    seed=None,
)
Indexer.update_once()
create_app(settings)
```

- Search returns stable segment identity, source/caption/analyzer provenance, source text, timing,
  match type and offsets, rank/score, and channel/video metadata.
- `order` is `ranked` or `random`; the same corpus/query/filter/seed produces the same random sample.
- Clip lookup returns everything the player needs for source playback, with optional fields reserved
  for the alignment and translation capabilities added by Plans 08 and 10.
- Channel mutations do not purge existing corpus data. A future or explicitly scoped purge command
  is separate and cannot be implied by `disable`.
- Translation enrichment is not part of search; Plan 08 defines it as an asynchronous clip action.

## Acceptance tests and verification

- Library tests exercise two simultaneous `Corpus` instances with independent settings and clean
  resource closure.
- CLI and HTTP contract tests cover search modes, limits/seeds, clip lookup, channel operations,
  statistics, validation, liveness, and readiness.
- A seeded `random` order returns the same sample twice and a different sample for a different seed.
- Mutable HTTP routes are unavailable by default and reject a missing/incorrect configured token;
  read-only local use requires no token.
- A subprocess smoke test starts `serve`, waits for readiness, searches a synthetic corpus, and
  terminates cleanly on a signal.
- `update --once` on an already-current synthetic corpus is safe to repeat and reports zero new work.
- API documentation clearly marks optional fields/capabilities and matches the checked-in OpenAPI
  contract.

## Non-goals

- A daemon manager, admin dashboard, user authentication system, hosted deployment, or public rate
  limiting service.
- Incremental indexing, job scheduling, or worker pause/resume; Plan 14 owns those.
- GraphQL, multiple API versions at launch, or abstractions for hypothetical transports.
- Translation, UI packaging, and ranking model implementation.
