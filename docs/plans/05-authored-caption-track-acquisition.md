# Plan 05: Authored caption-track acquisition

**Status:** Planned

**Depends on:** Plan 02

## Outcome

Acquire the best available caption track in a video's source language and retain useful
creator-authored tracks in other languages. The source transcript remains usable when an optional
secondary track fails, and YouTube's generated translations are not mistaken for authored data.

## Current state

- Acquisition requests Spanish captions and caches one successful transcript per video.
- Track metadata is not rich enough to distinguish authored captions, source-language automatic
  captions, creator translations, and generated translation offers throughout the pipeline.
- The completed English-track probe found that authored tracks could be downloaded reliably while
  advertised automatic translations failed under throttling.

## Decisions

- Select the canonical source track using the catalogue source language: prefer creator-authored
  captions, then original-language automatic captions. Record the chosen rule and actual result.
- Preserve each downloadable creator-authored subtitle track, regardless of target language, when
  it is returned directly by the provider. Exclude generated auto-translations from routine
  acquisition.
- Keep one source transcript as the only indexing input. Secondary authored tracks are optional
  reference/translation material and never replace source text implicitly.
- Keep the implementation provider-specific and small. Do not build a general subtitle-policy
  engine until another provider or a concrete exception requires one.

## Implementation work

1. Enumerate available tracks once per video and normalize their language, display name, provider
   track ID, authored/automatic kind, translatability flag, and source URL metadata.
2. Choose and download the canonical source track using a deterministic preference order and the
   existing retry/cache behavior.
3. Download directly available creator-authored secondary tracks independently. Record individual
   failures without failing or deleting a valid source transcript.
4. Store each track in a distinct cache entry and add a compact manifest linking the canonical
   source and optional secondary tracks to the video.
5. Carry caption provenance into transcript metadata, segments, reports, API results, and the demo's
   diagnostic display.
6. Extend acquisition reporting with source-track selection and authored-secondary coverage counts.

## Public interfaces and data

- A caption track records `track_id`, `language`, `kind` (`authored` or `automatic`), `is_source`,
  provider provenance, acquisition time, and content checksum.
- A video manifest names exactly one canonical source track when acquisition succeeds and zero or
  more secondary authored tracks.
- Source-language acquisition success is independent from secondary-track status.

## Acceptance tests and verification

- Fixtures cover authored-source preference, automatic-source fallback, multiple authored
  languages, generated translation exclusion, and ambiguous/missing language metadata.
- A secondary download failure still produces a valid source manifest and indexable transcript.
- Repeat acquisition reuses unchanged tracks and safely fills previously missing optional tracks.
- Reports distinguish authored source, automatic source, authored secondary, unavailable, and
  failed tracks without counting a generated translation as authored coverage.
- Existing source-only acquisition and search tests continue to pass.

## Non-goals

- Indexing translated text or using a target language to select source material.
- Downloading every generated YouTube translation, synchronizing tracks, or judging translation
  quality.
- Supporting arbitrary video providers before one is actually added.
