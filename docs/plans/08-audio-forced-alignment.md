# Plan 08: Audio forced alignment

**Status:** Planned

**Depends on:** Plans 06 and 07

## Outcome

Provide progressive timing for source-text character ranges when alignment is supported and
reliable, while retaining cue timing as an honest fallback. Prioritize useful viewed clips and
manual-caption material instead of requiring full-corpus alignment.

## Current state

- Search segments retain caption cue times but do not map individual source words or character
  groups to audio.
- Automatic captions often contain finer timing than authored captions, but their text can be less
  reliable; Plan 07 records the relevant trade-off.
- The earlier alignment note proposes WhisperX on bounded clips, confidence-aware caching, and a
  later Montreal Forced Aligner comparison.

## Decisions

- Define a narrow `Aligner` protocol around known source text, prepared audio, and language. The
  first optional implementation uses [WhisperX](https://github.com/m-bain/whisperX); capture the
  language/model support and limitations current at implementation time.
- Align the bounded clip around a segment before considering full-video work. Return timed source
  character ranges, unmatched ranges, coverage, confidence, and provenance.
- Use this priority order: a clip the user opens, remaining manual-caption videos, then automatic
  caption refinement when capacity is idle and Plan 07 shows a benefit.
- Fall back to original cue timing whenever a model is unavailable, the language is unsupported,
  audio is missing, or output does not meet documented sample-derived checks.
- Compare a small difficult sample with Montreal Forced Aligner before promoting WhisperX output
  broadly. Skip that comparison if the initial spike clearly fails the product need.

## Implementation work

1. Specify alignment input/output types and a dependency-free unavailable/fallback result.
2. Add WhisperX behind an `alignment` optional dependency, use explicitly installed models, and
   record package/model/device/settings versions without embedding the library throughout the app.
3. Convert model word spans into complete, ordered source character groups. Preserve punctuation,
   whitespace, unmatched text, and many-token/one-token mappings rather than fabricating precision.
4. Cache results by source-text hash, source language, audio checksum/range, aligner identity, model,
   and relevant settings. A failed result may be retried after its inputs or implementation change.
5. Expose alignment availability and progressive timing through clip lookup, with cue-level timing
   in the same response as fallback.
6. Connect work to the simple priority queue in Plan 09; allow a synchronous bounded request during
   development without inventing a separate alignment service.
7. Record a small benchmark against reviewed timing and, when warranted, MFA. Document coverage,
   boundary error, runtime, memory, supported languages, and the promotion decision.

## Public interfaces and data

- `Aligner.align(text, source_language, audio_clip)` returns versioned groups containing source
  character ranges, start/end time, confidence when meaningful, and match status.
- A clip reports `alignment_status`, `alignment_coverage`, `alignment_provenance`, and timed source
  groups; consumers always retain segment-level start/end times.
- Alignment cache data is derived and replaceable. Source captions and audio provenance remain
  linked but unchanged.

## Acceptance tests and verification

- Unit fixtures cover exact, punctuation-heavy, repeated-word, split/merged-token, partial, and
  unsupported-language output.
- Character groups are ordered, in bounds, and reconstruct or explicitly mark all source text.
- Opening an unaligned clip can prioritize work; missing models/audio returns cue timing without
  breaking playback.
- A dated experiment report compares the candidate against reviewed examples and records why it was
  promoted, limited to certain source classes, or left experimental.
- The ordinary test suite and exact subtitle-only player continue to work without alignment extras.

## Non-goals

- Phoneme-perfect timing, speaker diarization, or mandatory alignment of every indexed segment.
- Bundling alignment weights or requiring a GPU for core retrieval.
- Hiding low coverage behind interpolated word times.
