# Plan 06: Audio cache and media preparation

**Status:** Planned

**Depends on:** Plan 05

## Outcome

Add opt-in audio-only acquisition and reproducible 16 kHz mono clips for alignment and acoustic
experiments, while preserving the fast subtitle-only setup as the default.

## Current state

- The repository intentionally downloads no audio and derives timing only from captions.
- Alignment, ASR comparison, voice activity, speaking-rate, and quality experiments need local audio.
- Existing cache/report conventions already separate acquired inputs from rebuildable outputs.

## Decisions

- Audio is optional. Acquisition downloads an audio-only source only when `--with-audio` or a
  configured downstream feature requires it.
- Treat a successfully acquired source audio file as immutable input. Treat normalized full audio,
  extracted clips, waveforms, and features as replaceable derived data.
- Use ffmpeg to derive 16 kHz mono PCM clips around requested time ranges. Generate clips on demand
  and reuse them by source checksum, range, and preparation version.
- Begin with ordinary retry/backoff, checksums, disk-usage reporting, and an explicit prune command.
  Do not build a quota manager, cache daemon, or content-addressed storage system prematurely.
- Media or derived samples may be published when permission and licensing make that appropriate;
  repository defaults merely avoid committing large incidental caches.

## Implementation work

1. Add audio settings and CLI flags without changing the subtitle-only quick start.
2. Download the smallest suitable audio-only representation into the video's cache and record
   provider format, duration, size, checksum, acquisition time, and command/tool version.
3. Validate completed files before marking them ready; retry temporary failures with bounded
   exponential backoff and preserve useful diagnostics for permanent failures.
4. Add a clip-preparation function that accepts a video, start/end time, and optional padding and
   returns a cached 16 kHz mono WAV plus a manifest.
5. Report raw and derived storage by language/channel/video and provide a dry-run-capable command
   that prunes replaceable derived files. Raw source deletion requires an explicit separate flag.
6. Document expected disk use from a measured sample and the legal/provider considerations that a
   researcher should review for their intended corpus.

## Public interfaces and data

- Media manifests distinguish `raw_audio` and `derived_clip`, link every derivative to its source
  checksum, and record timing/preparation parameters.
- Library helpers expose audio availability, clip preparation, storage summaries, and explicit
  pruning; callers do not need to know the downloader's directory layout.
- Status can report audio enabled/disabled and ready/missing/failed counts without making audio a
  search prerequisite.

## Acceptance tests and verification

- Unit tests use tiny generated audio fixtures to verify conversion parameters, range padding,
  manifest reuse, checksum invalidation, and failure recovery.
- An opt-in real smoke test downloads one audio-only item, prepares a clip, and confirms it is mono,
  16 kHz, non-empty, and close to the requested duration.
- A default acquisition run creates no audio files and still builds the subtitle-only corpus.
- Storage reporting agrees with files on disk; dry-run pruning changes nothing and ordinary pruning
  leaves raw inputs intact.

## Non-goals

- Video download, streaming/proxying media to clients, or permanent publication of a scraped corpus.
- Automatic global disk quotas, cloud object storage, or distributed media processing.
- ASR, alignment, VAD, or quality scoring; later plans consume the prepared audio.
