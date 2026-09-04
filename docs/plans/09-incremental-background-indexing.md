# Plan 09: Incremental background indexing

**Status:** Planned

**Depends on:** Plans 04–08

## Outcome

Replace whole-corpus scripts with resumable, idempotent discovery, acquisition, segmentation,
analysis, indexing, alignment, and feature work. A simple foreground worker can update the corpus
periodically and report useful progress without becoming a distributed job platform.

## Current state

- Discovery and index building are separate whole-run scripts.
- Repeated acquisition reuses cached transcripts, but derived work is not tracked as explicit jobs
  and the SQLite index is rebuilt in full.
- There is no cooperative pause/shutdown, retry history, work priority, or per-stage progress model.

## Decisions

- Use SQLite as the only coordination store. Add a small job table and per-input stage versions;
  do not introduce Redis, a broker, distributed locks, or a workflow framework.
- Make `Indexer.update_once()` the primary unit: discover a bounded batch, enqueue missing/stale
  stages, perform available work, commit atomically, and return a structured summary.
- Identify work by operation, stable object ID, and pipeline version so repeated discovery is safe.
  Store `pending`, `running`, `succeeded`, `retryable`, `failed`, and `cancelled` states.
- Use bounded attempts with exponential backoff and jitter for temporary external failures. Manual
  retry remains possible; permanent failures stay observable instead of blocking unrelated work.
- Start with integer priority and FIFO within a priority. Viewed-clip alignment outranks ordinary
  backfill; discovery/acquisition needed for searchable content outranks optional features.
- Disabling a channel stops future discovery but preserves acquired/indexed material. Deletion is an
  explicit, separately confirmed purge command.

## Implementation work

1. Add schema-versioned channel/video/pipeline state and job records with uniqueness constraints
   that enforce idempotency.
2. Extract current discovery, caption acquisition, segmentation, analysis, and indexing into small
   operations that consume stable IDs and commit their output or failure consistently.
3. Update only affected videos/segments/occurrences and their aggregate counts. Retain `reindex` as
   a simple repair path when schema or analyzer changes make incremental migration unhelpful.
4. Implement claim/run/complete/retry transitions with recovery of abandoned `running` work after a
   process crash. Keep the single-process implementation correct before considering concurrency.
5. Add a foreground loop around `update_once()` with configurable interval, bounded cycle work,
   signal-aware shutdown, and cooperative pause/resume.
6. Add observable counts, oldest pending work, recent failures, current operation, last successful
   cycle, and per-language/channel freshness.
7. Integrate alignment and feature jobs only when those optional capabilities are configured;
   missing extras must not create endlessly failing jobs.

## Public interfaces and data

```python
summary = indexer.update_once()
```

- `Indexer.update_once()` is safe to call repeatedly and returns discovered, queued, completed,
  retried, failed, skipped, and remaining counts.
- Worker controls are cooperative `run`, `pause`, `resume`, and shutdown requests; process lifecycle
  remains the host supervisor's responsibility.
- Channel repository operations distinguish enable/disable from explicit purge.

## Acceptance tests and verification

- Running the same synthetic cycle twice creates no duplicate videos, tracks, segments,
  occurrences, or jobs.
- Adding one video processes only it; changing one pipeline version refreshes only affected derived
  stages or produces a documented full-reindex requirement.
- Tests cover temporary failure/backoff, permanent failure isolation, crash recovery, priority,
  pause/resume, clean signal shutdown, channel disable/re-enable, and explicit purge scope.
- Status remains responsive while work runs and gives enough information to diagnose stalled input.
- A measured local loop discovers and indexes a new test video without rebuilding unrelated rows.

## Non-goals

- Multi-host execution, exactly-once external effects, a general workflow engine, or a web queue UI.
- Self-daemonization, service installation, process restart, or container orchestration.
- Automatically deleting material when a channel is disabled or disappears upstream.
