# Plan 10: Forced alignment

**Status:** Planned

**Depends on:** Plan 09

## Outcome

Provide progressive timing for source-text character ranges when alignment is supported and
reliable, while retaining cue timing as an honest fallback. Prioritize useful viewed clips and
manual-caption material instead of requiring full-corpus alignment.

## Current state

- Search segments retain caption cue times but do not map individual source words or character
  groups to audio.
- Automatic captions carry finer timing than authored captions, but their text can be less reliable;
  Plan 09 records the relevant trade-off. Authored tracks currently have no sub-sentence timing at
  all, which is the visible gap in the player.
- Plan 09 supplies prepared 16 kHz mono clips and their provenance.

## Decisions

- Define a narrow `Aligner` protocol around known source text, prepared audio, and language. Aligning
  known text to audio is a CTC forced-alignment task, not a transcription task, and should not be
  approached by re-running ASR.
- Evaluate two families, and treat licensing as a first-class selection criterion because the host
  application is not a research artifact:
  - `torchaudio.functional.forced_align` with the
    [MMS_FA bundle](https://docs.pytorch.org/audio/stable/tutorials/forced_alignment_for_multilingual_data_tutorial.html),
    which covers 1100+ languages with a single model through uroman romanization. This is the best
    technical fit by a wide margin, but the MMS weights are **CC-BY-NC-4.0**, so it cannot be the
    default shipped path for a commercial host.
  - a permissively licensed path: per-language Apache-2.0 wav2vec2 CTC models with the same
    torchaudio alignment function, which is also how [WhisperX](https://github.com/m-bain/whisperX)
    aligns, or the Montreal Forced Aligner where its acoustic model license permits.
- Therefore: use MMS for the research numbers and the upper bound, and require the default
  configuration to be a permissively licensed model. Record every model's license in the report and
  in `doctor` output. Confirm the current license terms at implementation time rather than trusting
  this note.
- Align the bounded clip around a segment before considering full-video work. Return timed source
  character ranges, unmatched ranges, coverage, confidence, and provenance.
- Use this priority order: a clip the user opens, then remaining manual-caption videos, then
  automatic-caption refinement when capacity is idle and Plan 09 shows a benefit.
- Fall back to original cue timing whenever a model is unavailable, the language is unsupported,
  audio is missing, or output does not meet documented sample-derived checks. Never interpolate
  fake word times to hide low coverage.

## Implementation work

1. Specify alignment input/output types and a dependency-free unavailable/fallback result.
2. Add the aligner behind an `alignment` optional dependency with explicitly installed models,
   recording package/model/device/settings/license versions without embedding the library throughout
   the application.
3. Convert model word spans into complete, ordered source character groups. Preserve punctuation,
   whitespace, unmatched text, and many-token/one-token mappings rather than fabricating precision.
4. Cache results by source-text hash, source language, audio checksum/range, aligner identity, model,
   and relevant settings. A failed result may be retried after its inputs or implementation change.
5. Expose alignment availability and progressive timing through clip lookup, with cue-level timing in
   the same response as fallback, and render it through the props Plan 06 reserved.
6. Allow a synchronous bounded request during development and connect the work to Plan 14's priority
   queue when that exists, without inventing a separate alignment service.
7. Record a benchmark comparing the permissive path against MMS and against reviewed timing on a
   small difficult sample. Document coverage, boundary error, runtime, memory, supported languages,
   licenses, and the promotion decision. Skip the comparison if the initial spike clearly fails the
   product need.

## Public interfaces and data

- `Aligner.align(text, source_language, audio_clip)` returns versioned groups containing source
  character ranges, start/end time, confidence when meaningful, and match status.
- A clip reports `alignment_status`, `alignment_coverage`, `alignment_provenance`, and timed source
  groups; consumers always retain segment-level start/end times.
- Alignment cache data is derived and replaceable. Source captions and audio provenance remain linked
  but unchanged.
- Model identity in provenance includes its license so a downstream host can tell whether a stored
  alignment came from a non-commercial model.

## Acceptance tests and verification

- Unit fixtures cover exact, punctuation-heavy, repeated-word, split/merged-token, partial, and
  unsupported-language output.
- Character groups are ordered, in bounds, and reconstruct or explicitly mark all source text.
- Opening an unaligned clip can prioritize work; missing models or audio returns cue timing without
  breaking playback.
- A dated experiment report compares candidates against reviewed examples and records why one was
  promoted, limited to certain source classes, or left experimental.
- Configuration tests prove the default model selection is the permissively licensed one and that
  selecting a non-commercial model is a deliberate, recorded choice.
- The ordinary test suite and the exact subtitle-only player continue to work without alignment
  extras installed.

## Non-goals

- Phoneme-perfect timing, speaker diarization, or mandatory alignment of every indexed segment.
- Bundling alignment weights or requiring a GPU for core retrieval.
- Hiding low coverage behind interpolated word times.
- Re-transcribing audio; the source text is already known and is the input, not the output.
