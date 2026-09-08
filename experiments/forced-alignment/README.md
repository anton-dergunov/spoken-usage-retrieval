# Forced alignment against a recorded ASR timing reference

**Status:** Spanish complete and accepted, 2026-09-08 (`run-1`): metrics plus a 23-clip listening
pass. A rendering defect found during that pass is fixed and the fix is verified, but Spanish was
deliberately **not re-reviewed** — the reviewer judged the ranking stable and accepted the result.
English, Russian, and at least one language the reviewer does not speak are **required and not
started**; they need channel catalogues.

Relates to [Plan 10](../../docs/plans/10-forced-alignment.md).

## Decision this experiment exists to make

Authored caption tracks have no sub-sentence timing at all: `captions.py` collapses a manual
segment into one `TimedTextSegment` spanning the whole text, deliberately, "until forced alignment
supplies trustworthy word timing" (commit `9c913a1`). Automatic tracks already carry per-word start
times from YouTube's `tOffsetMs`.

So there are three questions, and the third is the one that matters:

1. Does CTC forced alignment produce timing good enough to render progressively?
2. What does choosing a permissively licensed model instead of the best available one cost?
3. **When is it worth running, and when is it not?**

## What is measured, and what it is not

There is no ground truth for word times. This experiment therefore reports **agreement**, never
accuracy, against three imperfect references:

- **YouTube's automatic-caption word start times**, parsed from json3 `tOffsetMs`. Real provider
  data, but it is another aligner's output. **Only start times are used**: `automatic_units`
  estimates each end as `min(start + 0.4, next_start)`, so scoring ends would measure this
  repository's own heuristic rather than YouTube's timing.
- **The cue-level baselines**, which are what the product does today and therefore the bar to beat.
  `cue_start` gives every word the cue's start time. `cue_interpolated` spreads words across the cue
  in proportion to character position — the strongest timing achievable with no acoustic model, and
  the honest comparison.
- **A human listening to the clips**, which decides where the numbers cannot.

Two methodological choices worth stating up front:

- **Median signed error is reported separately from median absolute error.** A constant lead or lag
  in the reference is a different problem from jitter, and conflating them would hide the single
  most interesting finding below.
- **Confidence intervals bootstrap over videos, not words.** When an alignment drifts it misses
  every word in that segment together, so treating ~1700 words as independent observations would
  report intervals several times tighter than the evidence supports.

For **authored** rows the reference is genuinely independent: it is a different track, produced by
a different system, from different text, and only words that match after normalization are scored,
so caption-versus-ASR text differences cannot contaminate the timing measurement.

For **automatic** rows the aligner and the reference operate on *identical text*. Those numbers
measure **how far re-alignment moves the times**, not whether the movement is an improvement.
Nothing in this section can say which is closer to the speech; that is what the human review is for.

## Frozen configuration

`config-v1.json`, pre-declared before the run. 100 segments per (language × source class) cell,
minimum 50, seed 20260908, segments of at least 4 tokens and at most 20 s, sampled round-robin
across videos so one talkative speaker cannot dominate a cell.

| System | Model | License |
| --- | --- | --- |
| `mms` | `MahmoudAshraf/mms-300m-1130-forced-aligner` | **CC-BY-NC-4.0** |
| `permissive` | `jonatasgrosman/wav2vec2-large-xlsr-53-spanish` | **Apache-2.0** |
| `cue_start` | none | — |
| `cue_interpolated` | none | — |

Licenses were verified against the HuggingFace model API on 2026-09-08, not taken from the plan.
One correction to Plan 10's text: the torchaudio VoxPopuli bundles that WhisperX uses as its
default aligners for `es`/`fr`/`de`/`it` are **also CC-BY-NC-4.0**, so "align the way WhisperX does"
is not the permissive route. The `jonatasgrosman` XLSR checkpoints are.

Romanization for the MMS path uses `uroman` (Ulf Hermjakob, USC ISI), whose license asks that
projects using it acknowledge it; this is that acknowledgement.

## Results, Spanish, 2026-09-08

Environment: MacBook Air M2, 24 GB, macOS 15.5, MPS. torch 2.14.0, torchaudio 2.11.0,
transformers 5.16.1, ffmpeg 4.4.4. 20 videos, 6614 indexed segments, 200 sampled, 10 videos per
cell across 4 channels.

Times in seconds. `<200ms` is the share of matched words whose start agrees within 200 ms.

| Cell | System | Segments | Videos | Words | median &#124;Δ&#124; | p90 | median signed | <200ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| es / authored | **mms** | 98 | 10 | 871 | **0.059** | 0.119 | +0.056 | **0.984** |
| es / authored | **permissive** | 98 | 10 | 871 | **0.063** | 0.125 | +0.061 | **0.972** |
| es / authored | cue_interpolated | 98 | 10 | 879 | 0.333 | 0.862 | +0.075 | 0.319 |
| es / authored | cue_start | 98 | 10 | 879 | 2.029 | 6.650 | −2.029 | 0.000 |
| es / automatic | **mms** | 96 | 10 | 820 | 0.077 | 0.179 | +0.075 | 0.928 |
| es / automatic | **permissive** | 100 | 10 | 839 | 0.081 | 0.198 | +0.078 | 0.909 |
| es / automatic | cue_interpolated | 100 | 10 | 840 | 0.317 | 0.713 | +0.021 | 0.318 |
| es / automatic | cue_start | 100 | 10 | 840 | 1.910 | 6.310 | −1.910 | 0.000 |

Engine-to-engine agreement, MMS against the permissive model over 1691 shared word pairs:
**median |Δ| 0.000 s, 88.9% within 200 ms.**

Runtime: 200 segments × 2 models in **90 seconds** wall clock, 0.023 s per audio-second, roughly
**43× realtime** on MPS. Peak resident memory stayed under 3 GB.

### Almost all of the "error" is a constant offset, not jitter

Both models are consistently *later* than YouTube's ASR by about 60–80 ms, and median signed error
is nearly equal to median absolute error, which means the sign almost never flips. Removing each
system's own median offset isolates the jitter:

| Cell | System | Constant offset | Jitter, median | Jitter, p90 | Within 100 ms after offset removal |
| --- | --- | ---: | ---: | ---: | ---: |
| authored | mms | +0.056 | **0.030** | 0.085 | **0.944** |
| authored | permissive | +0.061 | **0.029** | 0.086 | 0.933 |
| authored | cue_interpolated | +0.076 | 0.323 | 0.812 | 0.154 |
| automatic | mms | +0.075 | 0.038 | 0.118 | 0.859 |
| automatic | permissive | +0.078 | 0.036 | 0.122 | 0.843 |
| automatic | cue_interpolated | +0.021 | 0.317 | 0.701 | 0.167 |

This is the most informative number in the report. **The models agree with an independent reference
to within 30 ms of jitter** — one and a half emission frames, since wav2vec2 emits every 20 ms. They
are operating at the resolution limit of the architecture.

The baseline does not improve when its offset is removed (0.333 → 0.323), confirming that its error
is genuine jitter rather than a fixable constant. So the comparison is fair: the models are not
merely better-centred, they are better.

The offset itself is not evidence that either side is wrong. A forced aligner marks the acoustic
onset of a word; a caption timestamp is meant to be readable and tends to appear slightly early.
Which convention is *better for a learner* is a perceptual question, which is why it is in the
review rather than settled here.

## Interpretation

**Alignment is decisively worth it for authored captions.** Median error drops from 333 ms to 59 ms
and the share of words within 200 ms goes from 32% to 98%. That is not a marginal gain; it is the
difference between a highlight that visibly lags and one that tracks the voice. And it closes a gap
that is currently total: authored segments have no sub-sentence timing at all today.

**The non-commercial model buys almost nothing.** MMS beats the Apache-2.0 XLSR checkpoint by 4 ms
of median error and 1.2 points of within-200 ms — differences far smaller than the 30 ms jitter
floor, and far smaller than the interval the sample supports. Their median disagreement with each
other is *zero*. Practically: **the licensing question is answered, and it is free.** If this
project ever needs a commercial footing, switching profiles costs no measurable quality in Spanish.
That makes the CC-BY-NC-4.0 default a convenience rather than a dependency.

**Re-aligning automatic captions moves the times by about as much as the two models disagree with
each other.** 77 ms of movement against a 0 ms inter-model median is not evidence of improvement; it
is evidence that the aligner and YouTube's ASR broadly concur. Since automatic tracks already carry
word times for free, the burden of proof is on alignment to justify the compute, and these numbers
do not discharge it.

**Confidence does not predict timing error, and that is a negative result worth recording.** Mean
CTC probability per segment is flat against error across its whole range:

| Confidence quartile | Range | Median error |
| --- | --- | ---: |
| 1 (lowest) | 0.11–0.65 | 0.058 |
| 2 | 0.65–0.86 | 0.071 |
| 3 | 0.86–0.95 | 0.064 |
| 4 (highest) | 0.95–1.00 | 0.062 |

So the intuitive rule — "run alignment, then trust it only when confidence is high" — **does not
work**. Confidence is still useful as a *catastrophe* detector: the `MIN_MEAN_CONFIDENCE = 0.10`
gate rejected 6 MMS and 2 permissive segments outright. But among segments that clear the gate it
carries no information about how good the timing is. Any run/skip rule has to be built on something
else, and the thing that does work is much simpler: the source class.

### Failure cases

Only 8 of 400 model runs failed, all but two through the low-confidence gate:

| Reason | MMS | Permissive |
| --- | ---: | ---: |
| `low_confidence` (mean CTC probability below 0.10) | 5 | 1 |
| No overlapping reference words to compare against | 1 | 1 |

The second row is a measurement gap, not an alignment failure: the segment aligned fine, but the
ASR track had no matching words inside the clip window, so there was nothing to score it against.

The *worst* surviving authored segments are instructive precisely because they are not bad:

| Median error | Confidence | Text |
| ---: | ---: | --- |
| 0.13 s | 0.74 | `Una, dos y tres.` |
| 0.12 s | 0.86 | `Pero voy a beber.` |
| 0.12 s | 0.51 | `Qué planazo, qué sabroso.` |
| 0.12 s | 0.69 | `A ver qué tal.` |
| 0.11 s | 0.54 | `Ah, nos bajamos aquí.` |

Every one is a short segment of three to five words. With few words there are few anchors for the
sequence match, and a 60 ms constant offset is a larger share of a one-second utterance. Note also
that confidence ranges from 0.51 to 0.86 across these — more evidence that it is not the signal.
The worst case in the entire authored sample is 130 ms, which is at the edge of perceptibility
rather than broken.

## Deployment sizing (CPU only)

Measured on the same machine with `device=cpu` and `torch.set_num_threads(2)`, one model per
process, 12 clips each:

| Model | Seconds per audio-second | Speed | Peak RSS |
| --- | ---: | ---: | ---: |
| MMS 300M | 0.078 | 12.9× realtime | 2.18 GB |
| XLSR-53 Spanish | 0.077 | 13.0× realtime | 2.13 GB |

Two CPU threads on an M2 already run at 13× realtime, so the MPS path is a convenience rather than
a requirement. Extrapolating to a 4-core Zen NAS (Ryzen V1500B class, 2.0 GHz, AVX2), whose cores
are roughly a third of an M2 performance core on this workload, gives an estimated **4–5× realtime
on two threads** — about 100× faster than the "10 minutes per audio-minute" budget such a
deployment would need. Memory is the binding constraint rather than CPU, and 2.2 GB per worker
against 20 GB of RAM leaves ample headroom for one worker.

Two requirements carry over rather than being optional:

- **Chunk to ≤30 s windows.** wav2vec2 self-attention is quadratic in frames; a whole video in one
  pass will exhaust memory. `MAX_WINDOW_SECONDS` enforces this, and per-clip alignment is naturally
  within it.
- **Cap the worker.** One process, two threads, and a container memory limit, so background
  alignment cannot starve whatever else the box is doing.

These figures are extrapolations from one machine, not measurements on a NAS.
[Plan 16](../../docs/plans/16-deployment-portability.md) carries the actual porting work.

## Listening review (`run-1`)

20 clips plus 3 hidden repeats, blind A/B/C, audio only, rated by the author on 2026-09-08.

### The defect

The page fed the karaoke renderer `SystemResult.comparisons` — the subset of words that matched
the *scoring reference* — and then indexed it by display position. Any caption word the reference
ASR heard differently was dropped from that list, so every later word inherited the following
word's time and the tail of each sentence had no time at all. Concretely, for
`Por ejemplo, nosotros vamos a ir al Nuar.`, the word `ejemplo,` was highlighted at `nosotros`'s
time and the last two words never highlighted:

| Displayed word | Time it was shown with |
| --- | --- |
| Por | Por, 0.40 |
| ejemplo, | **nosotros, 0.75** |
| nosotros | **vamos, 0.97** |
| … | … |
| al, Nuar. | **none** |

Two consequences the reviewer reported independently and correctly: words with no time were never
highlighted (they rendered in italic), and the highlight appeared to *jump* over them. The
asymmetry in the tags follows directly — `jumps` was tagged 17/23 for MMS and 15/23 for the
permissive model, but only 2/23 for `cue_interpolated`, which assigns every word a time by
construction and so never skips one.

**The measured numbers in this report are unaffected.** The defect was in the review page only;
scoring reads the same `comparisons` list it always did, and the metrics are byte-identical after
the fix.

**The fix**, verified and in place for future runs: the page now renders from
`SystemResult.groups` — what each system actually produced — and highlights by **source character
range** rather than by word position, so a system timing a different number of words than the text
displays can no longer shift anything. Words that genuinely have no time (13 of 195 rows have any,
and never more than one or two) are carried along with the preceding word's highlight in a lighter
shade, so the highlight never blinks out and a gap is visible as a gap rather than as a skip.

### Why these ratings were accepted rather than re-collected

The reviewer rated **pace** and deliberately ignored the jumps, mentally interpolating across them.
The defect also applied *uniformly*: all three systems were rendered from the same matched subset
and shifted by the same words, so no system was advantaged. The **ranking** is therefore sound,
while the absolute sync quality is understated for every system. Read the ordering, not the levels.

A corrected Spanish pass was prepared and then deliberately not run: the reviewer judged the
ordering unlikely to move and accepted the result. The corrected renderer is instead carried into
the outstanding multilingual runs, where it will be exercised on fresh data.

| System | In sync | Slightly off | Broken | Mean (0–2) |
| --- | ---: | ---: | ---: | ---: |
| mms | 14 | 6 | 0 | **1.70** |
| permissive | 12 | 8 | 0 | 1.60 |
| cue_interpolated | 10 | 9 | 1 | 1.45 |

Agreement statistics: rating-versus-error Kendall's τ **0.24**, top-choice hit rate **0.80**,
reviewer self-consistency **1 of 3 repeats**. The self-consistency number is the one that matters
and it is poor — but with three repeats and a broken renderer it is not yet evidence of anything.

### The finding that survives, and it is the important one

Split by source class:

| Source class | cue_interpolated | mms | permissive |
| --- | ---: | ---: | ---: |
| authored | **1.20** | **1.70** | 1.60 |
| automatic | **1.70** | **1.70** | 1.60 |

**On authored clips the ear prefers alignment clearly. On automatic clips it cannot tell the
difference at all.** That is the same conclusion the measurements reach, arrived at independently.

The mechanism is worth stating because it explains why the baseline is not uniformly bad.
Automatic-caption segments are assembled from YouTube's word-level units, so their cues are short
and already tightly bounded — interpolating inside a short cue lands close. Authored cues are
display lines of one or two rows, so interpolating inside them is guesswork. The baseline is not
bad in general; it is bad exactly where authored captions are, which is exactly where alignment is
being proposed.

### The one number left open

The perceptual *margin* on authored clips: 1.20 versus 1.70 on a coarse three-point scale, from a
renderer that was understating all three systems. The measurement says 333 ms versus 59 ms, a
5.6× difference. The ear agrees on the direction but reports a smaller gap than the milliseconds
imply — which is the expected shape of the result, since perception of sync saturates well before
millisecond precision does. Quantifying that margin is deferred to the multilingual runs rather
than re-collected for Spanish.

## Recommendation

**Run alignment on authored-caption clips. Do not run it on automatic-caption clips.**

The rule is the source class, not a confidence threshold:

| Source | Action | Why |
| --- | --- | --- |
| Authored track | **Align** | 333 ms → 59 ms, and there is no sub-sentence timing otherwise. |
| Automatic track | **Skip** | YouTube's word times are free and agree within the models' own noise. |
| No audio, unsupported language, or below the confidence gate | Fall back to cue timing | Documented, visible, never interpolated. |

Two secondary recommendations:

- **Use the permissive profile unless there is a reason not to.** It costs 4 ms. Defaulting to the
  non-commercial model buys a difference smaller than the measurement floor while creating a cache
  that would have to be purged on any commercial move. The default stays MMS for now because this
  is a research corpus and the ceiling is worth knowing, but nothing depends on it.
- **Consider subtracting the measured constant offset** before rendering, or do not — but decide it
  deliberately after the human review, since the whole question is which convention reads better.

This recommendation covers Spanish only and rests on 4 channels and 20 videos. It should be
re-checked when the English and Russian catalogues land, and Russian in particular will test the
MMS romanization path in a way Spanish cannot.

## Limitations

- **Spanish only.** English and Russian are configured but have no channel catalogue yet.
- **Small video count.** 10 videos per cell across 4 channels. Per-channel medians agree closely
  (authored 0.056 / 0.059; automatic 0.073 / 0.082), which is reassuring, but this is not a
  speaker-diverse sample.
- **The reference is not truth.** Every authored number is agreement with YouTube's ASR aligner.
  Both could share a bias. The human review exists to catch exactly that.
- **The automatic-cell numbers cannot establish improvement**, only movement, because the aligner
  and the reference share their text.
- **Human review not yet imported.** Until it is, the perceptual questions — does 60 ms of lag
  read as late, and can a listener tell the baseline from the models — are open.
- No end-boundary claims are made anywhere, because the reference has no usable end times.

## How to run it

```bash
uv sync --extra dev --extra alignment

# 0. Corpus and audio (audio acquisition needs an operator-assessed legal basis).
uv run speech-retrieval update --once --limit 20
SPEECH_RETRIEVAL_WITH_AUDIO=true uv run speech-retrieval update --once --with-audio --limit 20

# 1. Inventory, then fetch the ASR reference for authored videos. The main pipeline stops
#    looking once it finds an authored track, so these are not in the corpus cache.
uv run python experiments/forced-alignment/run_alignment.py preflight
uv run python experiments/forced-alignment/run_alignment.py reference

# 2. Freeze the sample and measure. ~90 s for 200 segments across two models on an M2.
uv run python experiments/forced-alignment/run_alignment.py sample
uv run python experiments/forced-alignment/run_alignment.py align
uv run python experiments/forced-alignment/run_alignment.py score
uv run python experiments/forced-alignment/run_alignment.py report

# 3. The blind listening pass.
uv run python experiments/forced-alignment/run_alignment.py review-export
uv run python experiments/forced-alignment/run_alignment.py review-html
open data/experiments/forced-alignment/run-1/review.html
uv run python experiments/forced-alignment/run_alignment.py review-import \
    --worksheet ~/Downloads/alignment-review.filled.json
```

The 10-minute budget applies to `align`, `score` and `report`. First-time model download is about
2.5 GB and corpus acquisition is separate.

## Reviewing

`review.html` opens straight from disk: no server, no network, clips embedded as data URIs,
judgements in the browser's local storage, and a filled worksheet that downloads as JSON.

The design choices that make the result usable as evidence:

- **Audio only.** With video a reviewer can lip-read and can read burnt-in subtitles, both of which
  supply timing the alignment did not.
- **Blind and shuffled.** Each clip shows the three systems as A, B and C in an order randomized
  per clip; all six permutations occur. Which label is which is withheld until the row is submitted,
  the same anti-anchoring gate the caption-reliability review uses for its ASR text.
- **The baseline is in the blind set.** `cue_interpolated` is one of the three. If a listener
  cannot distinguish it from the models, that is the most useful finding the review could produce.
- **Three clips repeat** with a different permutation. Reviewer self-consistency is the ceiling for
  every agreement statistic; without it "human and engine agree 70%" has no denominator.

`review-import` then reports three numbers whose *comparison* is the point: engine-to-engine time
agreement (the noise floor), human-to-engine rank agreement, and human self-consistency. If the
engines agree with each other far more tightly than the human agrees with the measured ordering,
the millisecond thresholds are measuring something the ear does not care about, and the rule above
has to be rebuilt on perceptual categories.

## Artifacts

| Path | Committed? | Contents |
| --- | --- | --- |
| `config-v1.json` | yes | Frozen, pre-declared configuration |
| `config-schema-v1.json`, `result-schema-v1.json` | yes | Generated from the models; a test fails on drift |
| `results.json` | yes | Aggregated metrics, no caption text |
| `alignment_eval.py` | yes | Types, sampling, references, statistics |
| `run_alignment.py` | yes | Staged runner |
| `alignment_review_app.py` | yes | The blind karaoke page |
| `data/experiments/forced-alignment/run-1/` | no | Per-row JSONL, reference captions, review page |

## What must still be reported before this is complete

**Multilingual validation is the outstanding requirement.** Everything above is Spanish. The code
paths are wired and unit-tested for `es`, `en` and `ru` — `tests/test_alignment_languages.py` covers
model resolution, romanization and vocabulary folding, and its opt-in real-checkpoint tests
(`SPEECH_RETRIEVAL_ALIGNMENT_MODEL_TESTS=1`) load all six language/profile combinations — but no
other language has been measured against audio.

- **Channel catalogues for `en` and `ru`.** The only hard blocker. `config-v1.json` takes a
  `languages` list, the experiment emits one results table per language automatically, and
  `report --markdown` writes them ready to paste, so adding a language is configuration.
- **At least one language the reviewer does not speak.** Judging synchronisation does not need
  comprehension — you hear speech and watch a highlight — so this is feasible, and it is the only
  way to separate "the timing is good" from "I knew what was said and filled in the gaps." Such a
  run judges sync only; it cannot check whether the caption text itself is right.
- **Russian specifically**, because it is the case Spanish cannot test: MMS aligns through a
  romanized vocabulary, which in Spanish is near-identity accent folding but in Cyrillic is a real
  transliteration where one source character can become several romanized ones.
- Whether the constant offset should be corrected before rendering.
- A measured CPU run on the actual target hardware for
  [Plan 16](../../docs/plans/16-deployment-portability.md), replacing this report's extrapolation.
