# Plan 10: Library, CLI, and service API

**Status:** Planned

**Depends on:** Plans 02, 04, and 09

## Outcome

Stabilize one importable Python surface, a foreground command line, and versioned HTTP routes for
retrieval and corpus operation. Local research remains easy, while a host application can supervise
the same service without depending on repository scripts.

## Current state

- Package modules exist, but setup and operational behavior are still exposed primarily through
  scripts.
- FastAPI serves search and clip data for the demo without a declared versioned public contract.
- There is no complete channel-management, worker-control, readiness, or statistics surface.

## Decisions

- Keep the public Python surface small: `Corpus`, `Indexer`, `ChannelRepository`, settings, result
  types, and `create_app(settings)`. Other modules remain implementation details until reused.
- `speech-retrieval serve` and the worker loop stay in the foreground. Docker, systemd, or another
  host owns start, stop, restart, log collection, and persistent volumes.
- Version public HTTP routes under `/api/v1`. Add OpenAPI examples and contract tests before adding
  client conveniences.
- Bind to loopback by default. Mutating channel/indexer routes are disabled unless configured and
  require one operator token when exposed beyond a trusted local network. Do not build accounts,
  roles, sessions, or an authentication service.
- Support deterministic `ranked` ordering and reproducible `random` ordering with an optional seed.
  A limit always bounds materialized results.

## Implementation work

1. Consolidate settings and resource construction so library users, CLI commands, tests, and FastAPI
   share the same paths, catalogue, analyzer, and optional capability configuration.
2. Stabilize `Corpus.search(...)`, clip lookup, suggestions, corpus statistics, and close/context
   management with typed result models.
3. Stabilize `Indexer.update_once()` and channel repository list/add/update/enable/disable operations.
4. Implement CLI commands: `serve`, `update --once`, `search`, `channels
   list|add|update|enable|disable`, `status`, `reindex`, `models download|list`, and `doctor`.
   Commands should be thin adapters and support JSON output where automation benefits.
5. Add `/api/v1/search`, `/api/v1/clips/{segment_id}`, channel CRUD/activation, indexer
   run/pause/resume, statistics, status, and liveness/readiness endpoints.
6. Add consistent validation/error bodies, request IDs, modest request limits, and graceful resource
   shutdown. Keep observability to useful structured logs and status fields.
7. Generate and snapshot the OpenAPI document and add contract tests used by the React client in
   Plan 12.

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
- Channel mutations do not purge existing corpus data. A future or explicitly scoped purge command
  is separate and cannot be implied by `disable`.
- Translation enrichment is not part of search; Plan 11 defines it as an asynchronous clip action.

## Acceptance tests and verification

- Library tests exercise two simultaneous `Corpus` instances with independent settings and clean
  resource closure.
- CLI and HTTP contract tests cover search modes, limits/seeds, clip lookup, channel operations,
  worker controls, statistics, validation, liveness, and readiness.
- Mutable HTTP routes are unavailable by default and reject a missing/incorrect configured token;
  read-only local use requires no token.
- A subprocess smoke test starts `serve`, waits for readiness, searches a synthetic corpus, and
  terminates cleanly.
- API documentation clearly marks optional fields/capabilities and matches the checked-in OpenAPI
  contract.

## Non-goals

- A daemon manager, admin dashboard, user authentication system, hosted deployment, or public rate
  limiting service.
- GraphQL, multiple API versions at launch, or abstractions for hypothetical transports.
- Translation, UI packaging, and ranking model implementation.
