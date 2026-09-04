# Plan 09: Audio cache and caption-reliability benchmark

**Status:** Planned

**Depends on:** Plan 07

## Outcome

Add opt-in audio-only acquisition and reproducible analysis clips, then use them to answer a real
question: when can these captions be trusted? Produce a stratified WER/CER study against a recorded
ASR reference, a documented policy per source class, and the acoustic signals that later ranking work
needs. A useful negative result is a valid outcome.

The audio cache and the benchmark are one plan because the cache has exactly one consumer — this
experiment and the features it derives — and shipping media infrastructure with no measurement
attached would be infrastructure for its own sake.

## Current state

- The repository intentionally downloads no audio and derives timing only from captions.
- Both authored and automatic captions feed the same index without a measured reliability estimate.
- Cue boundaries and transcript errors can each harm retrieval, but the current corpus does not
  distinguish or quantify those failure modes.
- Alignment, ASR comparison, voice activity, speaking-rate, and clarity work all need local audio.
- Existing cache/report conventions already separate acquired inputs from rebuildable outputs.

## Decisions

### Audio cache

- Audio is optional. Acquisition downloads an audio-only source only when `--with-audio` or a
  configured downstream feature requires it. The subtitle-only quick start does not change.
- Treat a successfully acquired source audio file as immutable input. Treat normalized full audio,
  extracted clips, waveforms, and features as replaceable derived data.
- Use ffmpeg to derive 16 kHz mono PCM clips around requested time ranges. Generate clips on demand
  and reuse them by source checksum, range, and preparation version.
- Begin with ordinary retry/backoff, checksums, disk-usage reporting, and an explicit prune command.
  No quota manager, cache daemon, or content-addressed storage system.

### Benchmark

- Treat this as an experiment before adding production ASR. Use a configurable Whisper `large-v3`
  class model as the recorded reference baseline for a one-off run, not as unquestioned ground truth.
- Stratify a manageable sample by language, channel, caption provenance, speech style, and obvious
  acoustic conditions. Start with Spanish; add another language only to test a real hypothesis.
- Report WER for whitespace-tokenized languages and CER for languages such as Chinese and Japanese.
  Preserve both normalized and readable examples behind every aggregate.
- Add human qualitative tags for meaning-changing errors, missing/extra speech, names, disfluencies,
  and boundary problems. Metrics alone do not decide the policy.
- Choose policies from observed error patterns rather than setting universal quality thresholds in
  advance.

### Features this produces

- The run yields per-segment signals that Plan 12 can use, and naming them here is the reason the
  audio work is worth doing at all:
  - caption-to-ASR agreement per segment, a direct estimate of transcript trustworthiness;
  - speaking rate in tokens or syllables per second;
  - speech ratio from a lightweight VAD such as Silero;
  - a reference-free intelligibility and quality estimate from
    [TorchAudio-SQUIM](https://arxiv.org/abs/2304.01448), which predicts STOI, PESQ and MOS without a
    clean reference.
- SQUIM was trained to assess speech enhancement, not spontaneous YouTube audio, so a sample review
  must confirm its scores separate clips a human agrees are hard to follow before Plan 12 consumes
  them. Publish that check, positive or negative.
- These signals are also what a future "I want clearer speech" search preference would be built on;
  this plan measures them and does not add the preference.

## Implementation work

1. Add audio settings and CLI flags, download the smallest suitable audio-only representation into
   the video's cache, and record provider format, duration, size, checksum, acquisition time, and
   tool version. Validate completed files before marking them ready, retry temporary failures with
   bounded backoff, and keep diagnostics for permanent failures.
2. Add a clip-preparation function that accepts a video, start/end time, and optional padding and
   returns a cached 16 kHz mono WAV plus a manifest. Report raw and derived storage by
   language/channel/video and provide a dry-run-capable prune command; raw source deletion requires
   an explicit separate flag.
3. Write a versioned experiment configuration listing sampled segment/video IDs, random seed, strata,
   audio preparation, ASR model/settings, and text normalization.
4. Run ASR over the fixed clips and save hypotheses, timings, model provenance, runtime, and failures
   as replaceable experiment outputs.
5. Align caption and ASR text for scoring and calculate WER or CER with transparent normalization.
6. Label semantic and boundary discrepancies for representative cases, reusing Plan 11's review
   format if it already exists.
7. Report distributions and examples by language, channel, caption provenance, and speech/acoustic
   category, keeping per-item results so aggregates can be audited, and end with an explicit
   recommendation for each tested source class: use directly, attach a score, verify selectively,
   replace with ASR, or collect more evidence.
8. Add the four feature functions as independently optional helpers, each returning a value with its
   own provenance and version, and record the sample review that decides whether each one is
   trustworthy enough for Plan 12.
9. Document expected disk use from a measured sample and the legal and provider considerations a
   researcher should review for their intended corpus.

## Public interfaces and data

- Media manifests distinguish `raw_audio` and `derived_clip`, link every derivative to its source
  checksum, and record timing and preparation parameters.
- Library helpers expose audio availability, clip preparation, storage summaries, and explicit
  pruning; callers do not need to know the downloader's directory layout.
- Status can report audio enabled/disabled and ready/missing/failed counts without making audio a
  search prerequisite.
- The experiment adds configuration and result schemas, not a production API. Each result row
  identifies source language, video/segment, caption provenance, reference provenance, normalization
  version, error metric, qualitative tags, and reviewer status.
- Reports may be published with licensed snippets or with identifiers and aggregates as appropriate.

## Acceptance tests and verification

- Unit tests use tiny generated audio fixtures to verify conversion parameters, range padding,
  manifest reuse, checksum invalidation, and failure recovery.
- An opt-in real smoke test downloads one audio-only item, prepares a clip, and confirms it is mono,
  16 kHz, non-empty, and close to the requested duration.
- A default acquisition run creates no audio files and still builds the subtitle-only corpus.
- Storage reporting agrees with files on disk; dry-run pruning changes nothing and ordinary pruning
  leaves raw inputs intact.
- Metric tests cover insertions, deletions, substitutions, empty text, diacritics, and CJK character
  scoring.
- The experiment is rerunnable from its configuration and records missing media or model failures
  without changing the sample silently.
- Results contain both manual and automatic captions from multiple channels and state any important
  gaps in the intended strata.
- The report separates ASR disagreement from human-confirmed caption error and records the policy
  decision with supporting examples.
- Each acoustic feature has a recorded sample review and is marked usable or not usable for ranking.

## Non-goals

- Video download, streaming media to clients, or permanent publication of a scraped corpus.
- Automatic disk quotas, cloud object storage, or distributed media processing.
- Declaring ASR output to be perfect transcription or benchmarking every ASR model.
- Shipping continuous ASR verification before the experiment demonstrates value.
- Forced alignment, which Plan 10 owns, and ranking integration, which Plan 12 owns.
- Producing a statistically universal study of YouTube caption quality.
