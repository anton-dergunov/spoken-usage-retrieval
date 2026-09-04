# Translation and semantic alignment plan

## Goal

Show an English translation when a learner opens a clip, without translating the entire corpus in advance. YouTube English tracks are a best-effort shortcut rather than a dependency.

## Proposed path

1. Preserve the canonical source-language sentence and its timed caption segments.
2. When the viewer is requested, reuse a cached translation or make one structured LLM call that returns:
   - a natural English translation;
   - source-to-translation semantic alignment groups;
   - a schema-valid response with all source and target character ranges covered.
3. Cache the result by source-text hash, source/target language, model, and prompt version. Do not block source-language playback if enrichment fails.
4. Show the English sentence statically first. Experiment later with softly revealing aligned English groups alongside the timed Spanish groups, including many-to-one, one-to-many, reordered, and untranslated groups.
5. Request enrichment shortly before the viewer becomes visible where the host application can predict selection; otherwise show a quiet translation-loading state and stream the result into the open viewer.

## YouTube fallback

Prefer creator-authored English captions, then a successfully downloaded YouTube auto-translation. Align those cues to source segments by timestamp overlap. Fall back to the on-demand LLM call whenever the track is absent, inaccessible, or unsuitable. Store provenance so authored, YouTube-translated, and LLM translations can be evaluated separately.

YouTube translation must not be fetched on the viewer's critical path. If retained, acquire it as a throttled, retryable background optimization and cache successful responses permanently.

## Initial coverage probe

On 2026-09-04, all 10 cached videos advertised an automatic English track and three also advertised creator-authored English. All three authored tracks downloaded successfully as `json3`; all seven videos that depended on automatic English translation failed with HTTP 429, despite one-second subtitle delays. Alternate TV and embedded player-client probes did not recover the translated track.

The prototype should therefore use creator-authored English when it is already available, but use the single on-demand LLM translation-and-alignment call as the dependable default fallback. YouTube auto-translation remains optional experimental coverage rather than core infrastructure. The machine-readable local result is recorded in `data/reports/english-caption-coverage.json`.

## Evaluation

Measure translation latency, cache-hit rate, cost per viewed clip, alignment validity, word-order changes, idioms, omitted discourse markers, and learner preference between static and synchronized English.
