# Plan 14: Incremental background indexing

**Status:** Planned

**Depends on:** Plan 05, plus Plans 08–10 for the derived stages they add

## Outcome

Replace whole-corpus rebuilds with resumable, idempotent discovery, acquisition, segmentation,
analysis, indexing, alignment, and feature work. A simple foreground worker can update the corpus
periodically and report useful progress without becoming a distributed job platform.

This lands late deliberately. Plan 05's `update --once` plus a scheduled invocation is enough to keep
a small corpus fresh, so the job table, retry state machine and priority queue are a scaling
improvement rather than a prerequisite for using the system. Start this plan when full rebuilds
become slow enough to hurt, or when alignment and feature work create enough derived jobs to need
prioritizing.

## Current state

- `update --once` performs discovery, acquisition and a full index rebuild over enabled channels.
- Repeated acquisition reuses cached transcripts, but derived work is not tracked as explicit jobs
  and the SQLite index is rebuilt in full. The seed run produced about 30 MB for ten videos, so full
  rebuilds stop being reasonable within a few hundred videos.
- There is no cooperative pause/shutdown, retry history, work priority, or per-stage progress model.
- Plans 08–10 add derived stages (secondary tracks, audio, alignment, features) whose cost makes
  prioritization matter.

## Decisions

- Use SQLite as the only coordination store. Add a small job table and per-input stage versions; do
  not introduce Redis, a broker, distributed locks, or a workflow framework.
- Keep `Indexer.update_once()` as the primary unit and preserve its existing signature: discover a
  bounded batch, enqueue missing/stale stages, perform available work, commit atomically, and return
  a structured summary. Callers written against Plan 05 keep working.
- Identify work by operation, stable object ID, and pipeline version so repeated discovery is safe.
  Store `pending`, `running`, `succeeded`, `retryable`, `failed`, and `cancelled` states.
- Use bounded attempts with exponential backoff and jitter for temporary external failures. Manual
  retry remains possible; permanent failures stay observable instead of blocking unrelated work.
- Start with integer priority and FIFO within a priority. Viewed-clip alignment outranks ordinary
  backfill; discovery and acquisition needed for searchable content outrank optional features.
- Disabling a channel stops future discovery but preserves acquired and indexed material. Deletion is
  an explicit, separately confirmed purge command.
- Integrate alignment and feature jobs only when those optional capabilities are configured; missing
  extras must not create endlessly failing jobs.
- The worker stays a foreground process under host supervision. Add the reserved
  `/api/v1/indexer/pause` and `resume` routes here; process lifecycle remains the host's.

## Implementation work

1. Add schema-versioned channel/video/pipeline state and job records with uniqueness constraints
   that enforce idempotency.
2. Extract current discovery, caption acquisition, segmentation, analysis, and indexing into small
   operations that consume stable IDs and commit their output or failure consistently.
3. Update only affected videos, segments, occurrences and aggregate counts. Retain `reindex` as a
   simple repair path when schema or analyzer changes make incremental migration unhelpful.
4. Implement claim/run/complete/retry transitions with recovery of abandoned `running` work after a
   process crash. Keep the single-process implementation correct before considering concurrency.
5. Add a foreground loop around `update_once()` with configurable interval, bounded cycle work,
   signal-aware shutdown, and cooperative pause/resume.
6. Add observable counts, oldest pending work, recent failures, current operation, last successful
   cycle, and per-language and per-channel freshness, extending the statistics Plan 05 already
   exposes rather than inventing a second shape.
7. Enqueue derived work for the optional capabilities that are configured, including the viewed-clip
   alignment priority Plan 10 relies on.

## Public interfaces and data

```python
summary = indexer.update_once()
```

- `update_once()` is safe to call repeatedly and returns discovered, queued, completed, retried,
  failed, skipped, and remaining counts.
- Worker controls are cooperative `run`, `pause`, `resume`, and shutdown requests; process lifecycle
  remains the host supervisor's responsibility.
- Channel repository operations distinguish enable/disable from explicit purge.
- Statistics gain job-level fields without removing any field a host already consumes.

## Acceptance tests and verification

- Running the same synthetic cycle twice creates no duplicate videos, tracks, segments, occurrences,
  or jobs.
- Adding one video processes only it; changing one pipeline version refreshes only affected derived
  stages or produces a documented full-reindex requirement.
- Tests cover temporary failure/backoff, permanent failure isolation, crash recovery, priority,
  pause/resume, clean signal shutdown, channel disable/re-enable, and explicit purge scope.
- Status remains responsive while work runs and gives enough information to diagnose stalled input.
- A measured local loop discovers and indexes a new test video without rebuilding unrelated rows, and
  the measurement is compared against the full-rebuild time it replaces.
- Existing Plan 05 CLI and HTTP contract tests continue to pass unchanged.

## Non-goals

- Multi-host execution, exactly-once external effects, a general workflow engine, or a web queue UI.
- Self-daemonization, service installation, process restart, or container orchestration.
- Automatically deleting material when a channel is disabled or disappears upstream.
