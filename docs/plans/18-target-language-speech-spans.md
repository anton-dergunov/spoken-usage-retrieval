# Plan 18: Target-language speech spans from audio

**Status:** In progress — the spike is concluded on labelled real audio; promotion is still blocked
on impostor, CTC and DS923+ evidence

**Depends on:** Plan 09 (audio cache). Blocks passage extraction (Plan 17) for videos without
target-language captions or with interleaved lesson-language speech. Its costs feed Plan 16.

## Outcome

For any catalogue video, produce the time spans spoken in the catalogue's target language, each with
a transcript and word timings, from **audio and the target language alone**, at a precision a
learner can trust. These spans become an ASR unit source that Plan 17's passage strategies consume
like caption units.

## Current state

- The corpus is built from captions only. Many lesson channels have none in the target language, or
  caption only the lesson language.
- Lesson videos interleave the target language and a lesson language within seconds. Whole-file
  Whisper — multilingual or forced to the target — is not a language filter: on the spike's
  MinuteMandarin clip it labelled 59 of 59 segments Chinese while 158 of 242 s were English, and
  forced decoding translates isolated English into fluent target text.
- The spike [`experiments/target-language-speech-spans/`](../../experiments/target-language-speech-spans/README.md)
  built the pipeline, a synthetic code-switched benchmark with exact truth, a blind labelling server
  and a committed label store. **All 735 review units of the four real clips are now labelled**,
  so the pipeline has a real precision number rather than a caption proxy.

## Decisions (confirmed on labelled real audio)

- **Pipeline shape:** Silero VAD → VoxLingua107 ECAPA over 2 s windows (hop 0.5 s) → two-state
  Viterbi with a switch penalty → per-run gate requiring VoxLingua p(target) renormalised over the
  catalogue languages **and** Whisper language ID p(target) over the same set, both ≥ 0.7, minimum
  1 s → Whisper transcript with word timings for accepted runs only.
- **Whole-chunk decisions are rejected**: never above 0.76 span precision on synthetic truth.
- **The expensive model sees only candidate speech.** VAD and VoxLingua run on every second at under
  0.1 RTF on an M1; Whisper runs on accepted runs.
- **Precision over recall.** Single words and glosses inside lesson-language sentences are dropped
  (1 of 42 recovered on synthetic truth); long sentences are kept (66 of 66). On real audio the same
  effect is a length ladder: time recall 0.07 on target runs under 1 s, 0.82 on runs of 5 s or more.
- **Confirmed by the labels.** Pooled over the four real clips at the unchanged operating point:
  span precision **0.961** (123 of 128 judged spans) with **no hard failure**, time precision 0.986,
  time recall 0.722, long-run recall 0.792 — and **1.000 (18/18) on accepted spans of 5 s or more**.
  A diagnostic sweep of all nine methods against the same labels ranks `refined-agree` first by a
  wide margin; four of the five whole-chunk methods never reach 0.97 at any setting.

## Open questions this plan must close before promotion

1. ~~Real precision and recall from the owner's labels on the four spike clips.~~ **Closed**: see
   above. Two caveats carried forward — the pre-registered 0.97 precision floor is *not* met
   (0.961; raising the threshold to 0.8 would meet it but would be tuned on the only real labels),
   and under the worst-case reading that counts every `mixed` unit as non-target, precision is
   0.763. The labelled clips are fast-alternating lessons the corpus would filter out, so their
   recall number understates what to expect on catalogue videos.
2. False acceptance of confusable impostors (Catalan, Galician, Portuguese, Italian for Spanish;
   English for all) from public labelled speech — the closed-set score removes Catalan from the
   competitors by construction.
3. Whether CTC verification (forced-alignment confidence; Whisper vs CTC transcript agreement)
   removes the remaining English-glossed spans and translated transcripts. This is now the way to
   close the
   lenient/strict gap for **Spanish**, where the target and lesson language share a script; for
   Chinese the Han-script share of accepted spans already settles it (55 of 59 spans ≥ 90 % Han).
4. Cost on the DS923+ with one worker and capped threads; a Raspberry Pi measurement if the models fit.
5. Transcript quality of accepted spans (Whisper-small loops) and whether `medium` is affordable.

## Implementation work (after the questions above)

1. A `SpeechSpanDetector` in `src/speech_retrieval/` behind an optional extra, taking prepared audio
   and a language code, returning spans with scores, reasons and provenance.
2. An ASR unit source that turns accepted spans into `TimedUnit`s with `source="asr"` provenance.
3. A per-video, resumable job that never runs more than one heavy model at a time.
4. Documentation of the licence and attribution for each model.

## Verification

- The spike's tests (`tests/test_target_language_speech_spans.py`) plus unit tests for the
  production detector on hand-built detector outputs.
- The labelled spike clips reproduce the recorded precision within the reported uncertainty
  (`report` regenerates every number from the committed labels and the run directory, with no model
  re-run).
- A DS923+ run of one lesson video end to end, with RTF and peak RSS recorded.
