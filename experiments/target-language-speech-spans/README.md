# Target-language speech spans in mixed-language audio

**Status:** Complete for the question it asks. Started 2026-09-14; real-clip human labels finished
2026-09-20. The method selected on synthetic data holds up on all four labelled real clips
(pooled span precision **0.961**, 123/128, no hard failures). Still **not promotable**: no impostor
set, no CTC verification and no DS923+ cost measurement.

Prerequisite for [Plan 17](../../docs/plans/17-passage-extraction-comparison.md): passages can only
be cut from speech that is actually in the catalogue's language.

## Decision this experiment exists to make

Given **only a video's audio and its catalogue's target language**, can we find the time spans spoken
in that language — with a transcript and word timings — at high precision?

- **Generic, audio-only.** No captions, titles or per-channel rules reach the method. The four real
  clips are disposable examples; anything tuned to them is a failed spike. Captions are used only as
  an evaluation proxy.
- **Precision over recall.** Showing English — or Catalan — to a Spanish learner as Spanish is worse
  than missing a short fragment. Short fragments may be dropped freely: downstream needs complete
  sentences. Recall still matters for long spans.
- **The deployment target is a CPU-only Synology DS923+** (2 cores / 4 threads, no GPU) that is
  primarily a NAS and must stay responsive, with a Raspberry Pi as a stretch target. Larger models are
  measured as upper bounds, never assumed deployable.

## Why this is needed

- Many lesson channels have no captions in the target language, or captions that cover only the
  lesson language: the zh clip `TUJBWALllbo` captions its English speech and leaves the spoken Chinese
  examples uncaptioned.
- Speech interleaves the two languages within seconds and sometimes within one utterance
  ("认识 means to know someone").
- Forcing Whisper to the target language does not reject other-language speech; it **translates it
  into fluent target text**. Observed on `IniuZsvMTBM` with `small` and `medium`: the English line
  "before we start, subscribe and like" became `我们开始之前,请订阅并按赞`. A script check cannot
  catch that; only acoustic evidence can.

## Pipeline under test

```
audio ─▶ 16 kHz mono ─▶ Silero VAD ─▶ chunks (≤20 s) ─▶ per-chunk and 2 s sliding-window language ID
      ─▶ target-vs-rest score ─▶ switch-penalised Viterbi over windows (refined runs)
      ─▶ precision gate (score, minimum duration, transcript vetoes) ─▶ faster-whisper transcript
```

Detector scores compared (all take the target language as an input; none sees the "other" language):

| Score | Definition | Leans towards |
| --- | --- | --- |
| `vox` | VoxLingua107 ECAPA p(target) over all 107 languages | precision |
| `voxset` | p(target) renormalised over the 11 catalogue languages | recall on accented or Catalan-like Spanish |
| `voxpair` | p(target) / (p(target) + strongest rival) — a binary verification score | precision |
| `whisperset` | Whisper language-ID p(target) over the catalogue languages | comparison |
| `agree` | min(`voxset`, `whisperset`) | precision |

Operating points are chosen **on synthetic clips only** by a rule fixed in `config-v1.json` (maximise
recall of ≥3 s target runs subject to span precision ≥ 0.97), then applied unchanged to the real
clips.

## Evaluation data

- **Real clips** (`config-v1.json`): two zh and two es language lessons taught in English,
  2 508 s (41.8 min). Labelled blind by the owner through `review-serve` (audio only, per VAD unit:
  target / other / mixed / no speech / unsure), saved on every key press to the committed label
  store. **Labelling is complete: 735 of 735 units** — see "Manual review" below.
- **Synthetic lessons** with exact truth: seeded compositions from `synthetic/phrasebank-v1.json`
  over 15 pairs, including es↔pt, it↔es and fr↔it, rendered with Chatterbox multilingual cloning two
  English reference voices. macOS `say` voices were rendered first and **dropped**: VoxLingua
  recognised their de/fr/it/es/pt speech in 5 of 22 utterances against 36 of 37 for Chatterbox, and
  the English-voice "accented" condition sounded English (see `excluded` in the config). This check
  uses the detector under evaluation, so it is a sanity check on the voices, not independent truth.
- **Caption proxy** for `TUJBWALllbo` only: Latin-only cues ⇒ other, Han cues ⇒ target or mixed,
  uncaptioned speech ⇒ target. Biased by construction; reported separately, never used to tune.

## Run chronology

| When (BST) | What |
| --- | --- |
| 2026-09-14 19:56 | Probe on `IniuZsvMTBM`: Whisper large-v3 dual forced decoding too slow on CPU; stopped. `small`/`medium` probes, VoxLingua probe. |
| 2026-09-14 20:00–22:45 | Runner built; real-clip VAD and VoxLingua; first synthetic render with macOS voices (later dropped); memory exhaustion from unrelated applications slowed and invalidated timings; all jobs stopped and the laptop restarted. |
| 2026-09-14 22:50 | Closed-set (`voxset`) and pairwise (`voxpair`) scores added after inspecting Spanish outputs; `decide` rebuilt to join Whisper evidence by candidate id. |
| 2026-09-14 23:06 – 09-15 02:06 | One sequential chain: Whisper-small on real clips; remaining Chatterbox renders; VAD, VoxLingua and Whisper-small on 30 synthetic clips. |
| 2026-09-15 | `decide` and `report`. Scoring fix: spans with no judged speech are unjudged, not failures. **Selection-rule amendment** (below). Baselines W0/W1 on `IniuZsvMTBM` and the synthetic set. |
| 2026-09-14 23:38 – 09-20 12:14 | Blind labelling of all 735 review units through `review-serve`, audio only. |
| 2026-09-20 | Label-based metrics added — strict mixed reading, duration ladders, a labels-driven sweep of all nine methods, caption-proxy validation and a Han-script check on the zh spans; `report` re-run over the existing artifacts with no model re-runs. |

Run id `2026-09-14-spike`; Whisper `small` int8, beam 1, 4 threads; VoxLingua107 ECAPA
(`speechbrain` 1.1.1); Silero VAD 6.2.1; faster-whisper 1.2.1; all on an Apple M1 (16 GB), CPU only.

## Quality

### Synthetic truth (30 clips, 15 pairs × 2 voices; 898 s of target speech)

The operating-point sweep covers 9 methods × vetoes on/off × 6 thresholds × 4 minimum durations
(432 rows, `decide-small/sweep.json`). Denominators: *spans* = accepted spans with any truth speech
under them; *long runs* = target runs ≥ 3 s after joining pauses ≤ 1 s (100 runs).

| Method (best point meeting span precision ≥ 0.97, or best available) | Span precision | Long-run recall | Time recall | Spans |
| --- | --- | --- | --- | --- |
| `chunk-vox` / `chunk-voxset` / `chunk-voxpair` / `chunk-whisperset` / `chunk-agree` | 0.55–0.60 (**floor never met**; ≤ 0.76 at any setting) | 0.82–0.89 | 0.69–0.76 | 100–123 |
| `refined-vox` (p ≥ 0.5, ≥ 1 s) | 0.993 | 0.892 | 0.767 | 147 |
| `refined-voxpair` (p ≥ 0.5, ≥ 1 s) | 0.993 | 0.908 | 0.787 | 150 |
| `refined-voxset` (p ≥ 0.7, ≥ 1 s) | 0.994 | 0.985 | 0.864 | 156 |
| **`refined-agree` (p ≥ 0.7, ≥ 1 s) — selected** | **1.000** (148/148, 0 hard failures) | **0.985** (100/100 runs half-covered) | 0.854 | 148 |

- **Whole-chunk decisions cannot be precise.** VAD chunks join speech across pauses shorter than
  0.3 s, and lessons switch language at exactly such pauses, so a chunk is often mixed. Window
  refinement is required, not optional.
- **Every pair reaches span precision 1.00 at the selected point**, including es>pt, pt>es, it>es,
  es>it and fr>it; long-run recall 0.93 (en>de) to 1.00. The synthetic set therefore **does not
  separate the refined methods or the confusable pairs** — see Limitations.
- **Recall by utterance length** (utterance at least half covered): long sentences 66/66, sentences
  81/88, phrases 23/74, single words 11/38, words and phrases inside a lesson-language sentence 1/42.
  Short and inline fragments are dropped, as the precision-first design intends.
- Transcript vetoes (compression ratio ≤ 2.4, target-script share ≥ 0.5) changed nothing that
  mattered on synthetic data.

**Selection-rule amendment (disclosed).** The pre-registered rule maximised long-run recall and broke
ties by threshold then minimum duration. `refined-voxset` and `refined-agree` tied exactly on
long-run recall (0.98476) at the same threshold, and the rule picked `refined-voxset` (precision
0.994) over `refined-agree` (1.000). The tie-break was changed to prefer span precision first. This
was done **after reading real-clip transcripts but before any human label existed**; it reverses the
choice between two methods that differ only on synthetic precision.

### Real clips — human labels

All 735 review units of the four real clips are labelled, so the real precision claim no longer
rests on transcript reading. The operating point chosen on synthetic data is applied **unchanged**.

Denominators: *spans* = accepted spans; *judged* = accepted spans with `target` or `other` speech
under them (a span over silence or over `mixed` units alone cannot be judged either way);
*long runs* = `target` runs ≥ 3 s after joining gaps ≤ 1 s.

| Clip | Target | Audio | Units | Spans | Seconds | Judged | Span precision | Hard failures | Time precision | Time recall | Long-run recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `TUJBWALllbo` | zh | 824 s | 191 | 33 | 166 | 30 | **1.000** (30/30) | 0 | 1.000 | 0.761 | 0.777 (12/16 runs) |
| `IniuZsvMTBM` | zh | 249 s | 67 | 26 | 85 | 21 | 0.952 (20/21) | 0 | 0.982 | 0.850 | 0.852 (9/11) |
| `RubvgfZEVus` | es | 765 s | 212 | 27 | 81 | 25 | **1.000** (25/25) | 0 | 0.996 | 0.658 | 0.800 (13/17) |
| `xglEjH0Ue8o` | es | 670 s | 265 | 66 | 186 | 52 | 0.923 (48/52) | 0 | 0.966 | 0.665 | 0.777 (25/29) |
| **Pooled** | | 2 508 s | 735 | 152 | 518 | 128 | **0.961** (123/128) | **0** | 0.986 | 0.722 | 0.792 (59/73) |
| by language — zh | | 1 073 s | 258 | 59 | 251 | 51 | 0.980 (50/51) | 0 | 0.995 | 0.785 | 0.796 (21/27) |
| by language — es | | 1 435 s | 477 | 93 | 267 | 77 | 0.948 (73/77) | 0 | 0.977 | 0.662 | 0.785 (38/46) |

- **Precision falls short of the pre-registered 0.97 floor** (0.961) while synthetic data said
  1.000. The floor was set on synthetic truth and is not met on real audio — stated here rather
  than re-tuned away, because the labels are the only unbiased real evidence and were not used to
  choose anything (see "Which method wins on real audio").
- **No hard failures.** Not one accepted span is majority other-language. All five imprecise spans
  are 56–79 % target — a correct example sentence with an English gloss stuck to it, which is the
  one failure mode the 80 % criterion is meant to catch. They are the complete list:

  | Clip | Span | Target / other | Forced transcript |
  | --- | --- | --- | --- |
  | `IniuZsvMTBM` | 217.3–219.7 | 1.7 / 0.5 s (0.79) | `我是新来的` |
  | `xglEjH0Ue8o` | 250.8–254.0 | 2.1 / 0.9 s (0.70) | `NONE. No quedaba nada.` |
  | `xglEjH0Ue8o` | 267.8–269.6 | 0.9 / 0.7 s (0.56) | `Menos less.` |
  | `xglEjH0Ue8o` | 326.4–329.4 | 1.6 / 1.0 s (0.62) | `¿Por qué puedo? Porque hay gente. ¿Cuándo?` |
  | `xglEjH0Ue8o` | 595.9–599.1 | 2.3 / 0.7 s (0.77) | `Only. Solo me gusta el chocolate.` |

  Four of the five are on `xglEjH0Ue8o`, the clip taught by a strongly accented second-language
  speaker, and all five are 1.8–3.2 s — none reaches the 5 s band.
- **Recall is the weak side, by design**: 0.722 of target speech time, 0.792 within long runs.
  Whole missed runs are short ones — see the duration ladder.
- 24 of the 152 accepted spans have no `target`/`other` speech under them at all. Every one of them
  lies over `mixed` units, which is what the next section is about.

#### Mixed units: what the labels can and cannot say

A review unit is a raw Silero VAD piece (never merged across a pause, split at 6 s), so it can
record *that* both languages occur inside it, never *where*. Accepted spans are cut on a finer
grid: within a chunk, the Viterbi runs are split at **window midpoints, every 0.5 s**. The two
segmentations are deliberately different — the labels stay method-independent and reusable.

The method does use the finer grid. Across the 130 `mixed`/`unsure` units (523 s):

| | Units | Seconds |
| --- | --- | --- |
| Untouched by any accepted span | 91 | — |
| **Cut into** by an accepted span | 31 | — |
| Fully inside an accepted span | 8 | — |
| Mixed time inside accepted spans | | 90.8 of 522.9 = **17.4 %** |

Two readings are therefore reported, and the truth is between them:

| Reading | Judged spans | Span precision | Hard failures | Time precision |
| --- | --- | --- | --- | --- |
| **Lenient** (headline): mixed time is unjudgeable, excluded | 128 | **0.961** (123/128) | 0 | 0.986 |
| **Strict**: every mixed second counts as other-language | 152 | **0.763** (116/152) | 26 | 0.810 |

Strict is a worst case that charges a span for the full ambiguity of every mixed unit it touches,
including the 31 it cut into — where the labels cannot say whether the accepted part was the
target-language half. It is the right number to quote if the product must never show a learner a
span containing a foreign word; the lenient number is the right one for "did the method pick the
right side of the switch".

An **independent check breaks the tie for Chinese**, where a script test is possible: the share of
Han characters in the forced transcript of each accepted span needs neither the labels nor the
detectors.

| Clip | Spans | Mean target-script share | < 0.9 | < 0.7 | Worst |
| --- | --- | --- | --- | --- | --- |
| `TUJBWALllbo` | 33 | 0.987 | 2 | 1 | 0.677 |
| `IniuZsvMTBM` | 26 | 0.952 | 2 | 2 | 0.194 |

So on the zh clips 55 of 59 accepted spans are at least 90 % Han: the strict reading badly
overstates contamination there, and the lenient one is closer to the truth. **No equivalent check
exists for Spanish**, where the target and the lesson language share a script — the es
strict/lenient gap (0.948 vs 0.682 on `xglEjH0Ue8o`) stays unresolved by anything but relabelling.

#### Span length is what decides usefulness

Downstream needs a whole sentence, so a 1 s span matters far less than a 6 s one. Both precision and
recall improve with length, which is the behaviour the precision-first design wants.

| Accepted span length | Spans | Judged | Span precision | Hard failures |
| --- | --- | --- | --- | --- |
| 1–2 s | 26 | 25 | 0.960 (24/25) | 0 |
| 2–3 s | 51 | 40 | 0.975 (39/40) | 0 |
| 3–5 s | 55 | 45 | 0.933 (42/45) | 0 |
| 5–10 s | 16 | 14 | **1.000** (14/14) | 0 |
| ≥ 10 s | 4 | 4 | **1.000** (4/4) | 0 |

| Target-run length | Runs | Half covered | Time recall |
| --- | --- | --- | --- |
| < 1 s | 15 | 1 | 0.069 (1 of 12 s) |
| 1–2 s | 28 | 11 | 0.397 (16 of 39 s) |
| 2–3 s | 38 | 26 | 0.612 (54 of 88 s) |
| 3–5 s | 50 | 38 | 0.749 (128 of 171 s) |
| 5–10 s | 15 | 13 | **0.819** (75 of 91 s) |
| ≥ 10 s | 8 | 8 | **0.820** (138 of 169 s) |

**The product-relevant headline: of the 20 accepted spans of 5 s or more, 18 are judged and
18 are correct (1.000), with time precision 0.990.** Those 20 spans carry 124 s of target speech.
Recall on runs of 5 s or more is 0.82; the runs the method drops are overwhelmingly the sub-second
alternations that a learner could not use anyway (15 runs under 1 s contribute 12 s in total).

### Which method wins on real audio

The same sweep — 9 methods × vetoes × 6 thresholds × 4 minimum durations — scored against the human
labels. **This is diagnostic only.** The operating point was selected on synthetic truth in
`decide`, and nothing here feeds back into that choice; running it the other way would spend the
only unbiased real evidence on tuning. Best point per method under the same rule (span precision
≥ 0.97, then maximise long-run recall):

| Method | Best point | Span precision | Long-run recall | Time recall | Spans |
| --- | --- | --- | --- | --- | --- |
| `chunk-vox` | — | **floor never met** (best 0.969) | 0.357 | | |
| `chunk-voxset` | — | **floor never met** (best 0.926) | 0.575 | | |
| `chunk-voxpair` | vetoes, p ≥ 0.98, ≥ 1 s | 0.972 (35/36) | 0.369 | 0.294 | 36 |
| `chunk-whisperset` | — | **floor never met** (best 0.964) | 0.584 | | |
| `chunk-agree` | — | **floor never met** (best 0.968) | 0.513 | | |
| `refined-vox` | p ≥ 0.5, ≥ 4 s | 1.000 (23/23) | 0.277 | 0.213 | 24 |
| `refined-voxpair` | p ≥ 0.5, ≥ 4 s | 1.000 (23/23) | 0.317 | 0.243 | 25 |
| `refined-voxset` | p ≥ 0.5, ≥ 4 s | 0.974 (38/39) | 0.468 | 0.361 | 45 |
| **`refined-agree`** | p ≥ 0.8, ≥ 1 s | **0.975** (116/119) | **0.746** | 0.675 | 141 |

- **The synthetic choice is confirmed.** `refined-agree` reaches the precision floor with roughly
  **1.6× the long-run recall of the best rival** that also reaches it, and it is the only method
  that gets useful recall at high precision. The other refined methods buy precision by accepting
  almost nothing (24–45 spans against 141).
- **Whole-chunk decisions fail on real audio too**: four of the five `chunk-*` methods never reach
  0.97 at any of their 48 settings, matching the synthetic finding that window refinement is not
  optional.
- **Real data separates the methods where synthetic data could not.** On synthetic clips four
  refined methods scored 0.99–1.00 precision at 0.89–0.99 long-run recall and the ranking was
  almost arbitrary; here they spread from 0.277 to 0.746 long-run recall.
- The threshold ladder for `refined-agree` (vetoes off, ≥ 1 s) shows what the floor costs:

  | p ≥ | Span precision | Hard failures | Long-run recall | Spans |
  | --- | --- | --- | --- | --- |
  | 0.5 | 0.946 (139/147) | 2 | 0.834 | 174 |
  | **0.7 — in use** | **0.961** (123/128) | **0** | **0.792** | 152 |
  | 0.8 | 0.975 (116/119) | 0 | 0.746 | 141 |
  | 0.9 | 0.971 (100/103) | 0 | 0.573 | 122 |
  | 0.95 | 0.988 (84/85) | 0 | 0.425 | 96 |
  | 0.98 | 0.984 (60/61) | 0 | 0.345 | 69 |

  Raising the threshold to 0.8 would buy the floor (0.975) for 0.046 of long-run recall. **It has
  not been adopted**: it would be a change tuned on the only real labels in existence, and the
  numbers above would no longer be an unbiased estimate of anything. It is the obvious candidate for
  the next round, to be confirmed on clips labelled after the fact.

### The caption proxy, now checkable

The `TUJBWALllbo` caption proxy was the only real evidence in the previous round and was declared
biased by construction. Against the human labels its target intervals score **span precision 0.970
(64/66), time precision 0.991, time recall 0.976, long-run recall 0.979** — a better stand-in than
assumed, though it still needs an other-language caption track and a non-Latin target, so it cannot
generalise to Spanish. Its two "hard failures" against the method (`你知道吗?`, `他明天也`) were
proxy errors, exactly as the transcript reading suspected: the human labels call those spans
Chinese, and the method scores 1.000 on this clip.

### Transcript reading (superseded, kept for the record)

Before the labels existed, every accepted span's forced transcript was read (Chinese, Spanish,
English). It agreed with the labels in direction but was systematically optimistic about the es
clips, where it could not hear the English glosses that the strict reading now counts. What it did
establish independently still stands:

- **The closed set matters for Spanish.** On `RubvgfZEVus`, `refined-vox` accepts 4 spans (10 s) and
  `refined-voxset` 28 (84 s). The extra spans are Spanish sentences that VoxLingua labels Latin,
  Esperanto or Catalan (e.g. `cuando era estudiante` — la 0.93, es 0.04) while Whisper's language ID
  says es 0.99. The open-set score throws away most real Spanish from these speakers.
- **The agreement requirement removes the English-mixed Spanish spans** that `voxset` alone accepts:
  `más more Dame más` (Whisper en 0.52), `También also.` (en 0.32), `¡Jobio mucho!` (it 0.93).
- Whisper-small transcripts of accepted zh spans sometimes loop (`男朋友是因为他不够主动` ×3): a
  transcript-quality issue for the downstream stages, not a language error.

### Naive Whisper baselines (`IniuZsvMTBM`)

| Baseline | Segments labelled zh | Of those, text mostly Latin script (English) |
| --- | --- | --- |
| W0 — whole file, `multilingual=True` | 59 of 59 | 35 segments, 158 of 242 s |
| W1 — whole file, `language="zh"` | 72 of 72 | 42 segments, 142 of 232 s |

Scored against the human labels on the same clip, where the selected pipeline reaches 0.952 span
precision and accepts 1.2 s of English:

| Baseline | Spans | Judged | Span precision | Hard failures | Time precision | Time recall | English accepted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W0 — multilingual | 59 | 48 | 0.458 (22/48) | 24 | 0.557 | 0.991 | 59 s |
| W1 — forced target | 72 | 47 | 0.532 (25/47) | 21 | 0.547 | 0.936 | 58 s |
| **Selected `refined-agree`** | 26 | 21 | **0.952** (20/21) | **0** | **0.982** | 0.850 | **1.2 s** |

Whole-file multilingual mode never switched language on this clip, so as a language filter it would
accept 158 s of English as Chinese. Forced decoding kept long English stretches in English here, but
translated isolated English chunks into fluent Chinese in the chunk-level probe — whether English
survives depends on context, so neither output is a language decision. 

On the 30 synthetic clips (exact truth; the rows are accepted segments):

| Method | Span precision | Time precision | Time recall | Other-language speech accepted |
| --- | --- | --- | --- | --- |
| W0 — whole file, multilingual | 0.333 (43/129) | 0.42 | 0.19 | 233 s |
| W1 — whole file, forced target | 0.367 (255/695) | 0.43 | 1.00 | 1 196 s |
| **Selected `refined-agree`** | **1.000 (148/148)** | **0.99** | 0.85 | **6 s** |

## Manual review: rubric, conditions and what was actually judged

**What was reviewed.** Every one of the 735 review units of all four real clips, by the repository
owner (the only annotator), through `review-serve` between 2026-09-14 23:38 UTC and
2026-09-20 12:14 UTC. No unit was skipped, sampled or batch-filled; each label was written to the
store on the key press that produced it.

**Conditions.** Blind and audio-only: the page plays one unit and shows five keys. No transcript,
no detector score, no method decision and no neighbouring-unit label is visible, and the store
records this (`provenance.blind`). Unit times come from Silero VAD 6.2.1 with the committed settings
over the prepared 16 kHz mono audio, whose sha256 is recorded per clip. A test asserts the review
page is offline and shows nothing but audio.

**Rubric v1**, verbatim as shown to the reviewer:

| Key | Label | Anchor |
| --- | --- | --- |
| 1 | Target language | "Everything audible in the unit is the clip's target language. A name, a brand or one borrowed word inside it does not change that." |
| 2 | Other language | "Everything audible is some other language, usually the language the lesson is taught in." |
| 3 | Mixed | "Both the target language and another language are audible inside this unit, for example 'the word 认识 means to know'." |
| 4 | No speech | "Only music, noise, laughter, breathing or a sound effect; no words at all." |
| 5 | Unsure | "You replayed it and still cannot tell which language it is, or it is too short to judge." |

**Label distribution** (735 units): target 272, other 331, mixed 129, no speech 2, unsure 1.

| Clip | target | other | mixed | no speech | unsure |
| --- | --- | --- | --- | --- | --- |
| `TUJBWALllbo` | 67 | 105 | 18 | 1 | 0 |
| `IniuZsvMTBM` | 28 | 17 | 21 | 0 | 1 |
| `RubvgfZEVus` | 48 | 126 | 38 | 0 | 0 |
| `xglEjH0Ue8o` | 129 | 83 | 52 | 1 | 0 |

**What the labelling is worth, and what it is not.**

- **One annotator, no agreement measure.** The reviewer is the experiment's owner. Nothing here
  measures inter-annotator agreement or intra-annotator consistency, and the reviewer knew the
  purpose of the task while labelling (not which spans the method had accepted).
- **Only 3 units of 735 were hard to judge** (2 no speech, 1 unsure), so ambiguity is concentrated
  in `mixed`, not in reviewer uncertainty.
- **The unit grid limits what a label can localise** — see "Mixed units" above. This is the single
  largest source of uncertainty in the precision numbers.

### What the labelling showed about these clips

Observations from the reviewer, recorded because they change how the numbers should be read:

- **Exactly two languages per clip, and no others.** English throughout, plus Mandarin on the two zh
  clips or Spanish on the two es clips. No third language occurs anywhere in the four clips — not a
  phrase, not a sentence. (Isolated conventional tokens such as "etc." are not a language switch.)
- **This retires the Catalan/Latin reading of the earlier round.** Where VoxLingua scored
  `cuando era estudiante` as Latin 0.93 / Spanish 0.04, the audio is plain Spanish. Those were
  **detector confusions on real speech, not content** — which strengthens rather than weakens the
  case for the closed-set score, since the open-set `vox` discards most genuine Spanish from these
  speakers (4 spans / 10 s against 28 / 84 s on `RubvgfZEVus`). It also sharpens the impostor risk:
  **a detector that already puts native and accented Spanish near Catalan and Latin cannot be
  assumed to reject actual Catalan**, and `voxset` removes Catalan from the competitors by
  construction. Untested, and now a known-shaped hole rather than a hypothetical one.
- **Speakers and accents**, which is what the detector is actually up against:
  - both zh clips: native Mandarin speakers (two different people), a slight accent in their
    English, effectively none in their Chinese;
  - `RubvgfZEVus`: a native Spanish speaker from Spain — Peninsular pronunciation, clearly not Latin
    American — with an accent in her English;
  - `xglEjH0Ue8o`: a native English speaker (American) teaching Spanish with a **strong and
    immediately audible** accent in Spanish — a second-language teacher, not a native model. This is
    the clip with the lowest span precision (0.923) and the most mixed units touched (18 spans).
- **These clips are a stress case, not representative catalogue input.** They are lessons *about*
  the target language taught in English: perhaps 5 % of their speech is target-language example
  material, the two languages alternate every few seconds, and sometimes a single word is quoted
  inside an English sentence. The owner would **exclude channels like these** from the corpus, whose
  purpose is finding words used naturally in connected speech. They were chosen precisely because
  they are short and switch constantly, which makes them hard for a span detector and therefore a
  good test of switch detection — and a poor basis for predicting recall on the
  mostly-target-language channels the product will actually index, where runs are long and switches
  are rare. Expect the
  measured recall (0.722 of target time) to be pessimistic for real catalogue videos, and the
  precision to be optimistic in one respect only: these speakers are teachers recorded cleanly.

## Cost

Apple M1, CPU only, one stage at a time after the restart; **not DS923+ numbers**. RTF = compute
seconds ÷ audio seconds.

| Stage | Audio | RTF | Peak RSS | Notes |
| --- | --- | --- | --- | --- |
| Silero VAD + chunking | 5 289 s | 0.008–0.016 | 0.4 GB | |
| VoxLingua107 ECAPA, chunks + 2 s windows (hop 0.5 s, batch 4) | 5 031 s | 0.07–0.09 | 0.9–1.5 GB | ~20 M-parameter model; batch 16 was pathologically slow |
| Whisper-small language ID + forced transcript of every candidate | 2 523 s synthetic | 1.10 | 1.3 GB | Inflated: overlapping candidates from three window scores were each decoded (3 380 s decoded); production decodes accepted spans once |
| Whisper-small, whole-file W0 + W1 | 498 s | 0.16 | 1.6 GB | Two passes |

The first real-clip Whisper-small timings were taken under memory exhaustion and after resumption,
so they are not quoted. For the NAS, the shape is what matters: VAD and VoxLingua run on every second
of audio at well under a tenth of real time on the M1; Whisper runs only on candidate or accepted
speech. The DS923+ (2 cores) must be measured before any promotion; expect it to be several times
slower than the M1.

## Decision

- **Adopt the shape:** Silero VAD → VoxLingua on 2 s windows with a switch-penalised Viterbi →
  per-run gate requiring **both** VoxLingua (closed over the catalogue languages) **and** Whisper
  language ID ≥ 0.7, minimum 1 s → Whisper transcript of accepted runs only.
- **`refined-agree` is confirmed on real audio**, and by a wider margin than on synthetic data: it
  is the only method that reaches the precision floor with usable recall (0.746–0.792 long-run
  recall against 0.277–0.468 for every other method that reaches it).
- **Do not use whole-chunk decisions, whole-file multilingual Whisper, or forced-language Whisper as
  a language filter.** On labelled real audio the two Whisper baselines score 0.458 and 0.532 span
  precision and accept ~58 s of English as Chinese on a 249 s clip.
- **Expected quality, stated honestly:** pooled span precision **0.961** (123/128) with no hard
  failure, **1.000 on spans of 5 s or more** (18/18), against **0.763** under the worst-case reading
  of mixed units; time recall 0.722, long-run recall 0.792, rising to 0.82 on runs ≥ 5 s.
- **Still not promotable.** Three of the five open questions are untouched: no impostor set, no CTC
  verification, and no DS923+ measurement. The precision floor of 0.97 is *not* met at the point in
  use (0.961); raising the threshold to 0.8 would meet it on these labels but would be tuned on
  them.

## Limitations

- **Synthetic truth is easy.** Clean TTS, one speaker, and a pause of ≥ 60 ms at every switch. Every
  pair scores ~1.0, so it validates plumbing and length effects but cannot rank methods or confusable
  pairs. Real lessons glue glosses to examples without pauses. The real labels rank the methods that
  synthetic truth could not separate.
- **Selection on synthetic, amended once** (see above); the real clips were looked at before the
  amendment. The real sweep is reported but was not used to select anything.
- **One annotator, no agreement measure**, and the labelling grid cannot localise a switch inside a
  unit — which is why precision is reported as a range (0.961 lenient / 0.763 strict) rather than a
  point. Only the zh half of that range is narrowed by independent evidence (script share); for
  Spanish it stands unresolved.
- **The 0.97 span-precision floor is not met on real audio** at the operating point in use.
- **Catalan is untested as an impostor**, and the labels show the risk is real rather than
  theoretical: VoxLingua already places genuine Spanish near Catalan and Latin, and `voxset` removes
  Catalan from the competitors by construction.
- **The 80 % span criterion counts one-word English glosses as correct**; the strict reading is the
  only bound reported on them.
- **Four real clips from two lesson channels per language, two target languages**, all
  fast-alternating lessons the product would filter out (see "What the labelling showed"). No street
  speech, music, overlapping speakers or other target languages. Recall on real catalogue videos is
  likely better than measured; precision on noisier audio is unmeasured.
- **Costs are M1 numbers**, partly inflated (redundant decoding) and partly unmeasured (DS923+).

## Next round

1. Impostor set from public labelled speech (FLEURS or Common Voice: ca, gl, pt, it for es; en for
   all) to measure false acceptance per method — the largest remaining risk.
2. Two CTC verifiers on accepted spans: forced-alignment confidence of the Whisper transcript with a
   target-language CTC model (the repository's aligner already rejects low confidence), and
   character-error agreement between Whisper and an independent CTC transcript (MMS adapters or
   Omnilingual CTC 300M). Target: close the lenient/strict gap on Spanish, where no script check
   exists.
3. Measure the selected pipeline on the DS923+.
4. Decide the threshold (0.7 vs 0.8) on clips labelled *after* the decision, not on these.
5. Label a handful of clips from channels the product would actually index — mostly target-language
   speech, rare switches — to get a recall number that means something for the corpus.
6. A stricter span criterion (no other-language unit at all) alongside the 80 % one, and finer
   labelling of the mixed units that accepted spans cut into.

## Prior work and sources

This problem is established; the spike applies known techniques to lesson-style audio and calibrates
them for a precision-first product. It does not propose a new model.

**Target-language detection as a binary task.** NIST's Language Recognition Evaluations define the
detection task — given a segment and a target language, decide whether the target was spoken — and
score pairwise miss/false-alarm costs (Cavg). LRE15 scored within clusters of closely related
languages, the analogue of Spanish vs Catalan.
- [The 2017 NIST Language Recognition Evaluation](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=925272)
- [LRE15 evaluation plan](https://www.nist.gov/system/files/documents/2016/10/06/lre15_evalplan_v23.pdf)
- [NIST 2007 LRE from the perspective of IIR](https://aclanthology.org/Y08-1004.pdf)

**Code-switched language identification and diarization.** The MERLIon CCS challenge benchmarks
LID and language diarization on English–Mandarin code-switched, accented speech with very short
utterances — the closest public analogue to the zh lesson clips.
- [MERLIon CCS Challenge (Interspeech 2023)](https://www.isca-archive.org/interspeech_2023/chua23_interspeech.html)
- [MERLIon CCS evaluation plan](https://arxiv.org/pdf/2305.19493)

**Language-ID models and embeddings.** The standard recipe for a per-language verifier is a speech
embedding with a logistic-regression back end; accents degrade LID noticeably.
- [VoxLingua107 dataset paper](https://arxiv.org/pdf/2011.12998) ·
  [speechbrain/lang-id-voxlingua107-ecapa](https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa)
- [facebook/mms-lid-256](https://huggingface.co/facebook/mms-lid-256) — 1B wav2vec2 LID
- [Accent and dialect identification with multi-embedding models](https://arxiv.org/pdf/2310.11004)
- [How speech embeddings reflect linguistic relations (PLOS One)](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0330755)

**Why Whisper translates, and ASR that cannot.** Whisper is an autoregressive decoder conditioned on
a language token; users report it translating instead of transcribing, and it assumes one language
per 30 s window. CTC models emit characters frame by frame from the acoustics, so a Spanish-conditioned
CTC model given English speech produces phonetic garbage with low confidence rather than fluent
Spanish — a detectable failure.
- [whisper#2285: forcing a language translates](https://github.com/openai/whisper/discussions/2285) ·
  [whisper#49: mixed-language transcription](https://github.com/openai/whisper/discussions/49)
- [MMS in Transformers](https://huggingface.co/docs/transformers/en/model_doc/mms) — CTC with ~2M-parameter per-language adapters
- [Omnilingual ASR paper](https://arxiv.org/pdf/2511.09690) · [code](https://github.com/facebookresearch/omnilingual-asr) — Apache-2.0 CTC models from 300M, plus LLM-decoder variants with optional language conditioning
- [OWSM-CTC](https://arxiv.org/pdf/2402.12654) — open encoder-only ASR, translation and LID
- [Script collapse in multilingual ASR](https://arxiv.org/pdf/2604.08786) — reference-free detection of wrong-script output
- [Open ASR Leaderboard paper](https://arxiv.org/html/2510.06961v4) · [2026 open STT overview](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks) — Canary-Qwen, Qwen3-ASR, Voxtral

**Frontier audio models as an upper bound.** Gemini accepts audio input and documents automatic
language identification that follows code-switching; usable as a non-promotable ceiling or judge,
not as a NAS component.
- [Gemini API audio understanding](https://ai.google.dev/gemini-api/docs/audio) ·
  [Gemini API transcription](https://ai.google.dev/gemini-api/docs/transcribe)

## Reproduction

```sh
uv sync --extra speech-span-experiments
uv run --script experiments/target-language-speech-spans/synth_tts.py \
  --out data/experiments/target-language-speech-spans/synthetic      # macOS / Apple silicon only
R="uv run --extra speech-span-experiments python experiments/target-language-speech-spans/run_speech_spans.py"
$R audio          # real clips from ~/tmp (--media-dir) plus the synthetic manifest
$R chunks && $R voxlingua && $R whisper
$R decide
$R review-export
$R review-serve   # http://127.0.0.1:8765/ — every label is written to labels/ immediately
# Offline alternative: $R review-html, then review-import --worksheet <downloaded file>
$R report --captions-dir data/experiments/target-language-speech-spans/captions
```

Run heavy stages one at a time; every stage skips work already on disk. The labels are committed, so
`report` reproduces every number in this file from the run directory alone — it loads no model and
touches no network. It refuses to run if the label store does not match this run's review units.

`results.json` beside this file is that report committed verbatim, plus a note; it carries no
transcript text by construction. Regenerate it with `$R report --write-results` instead of copying
it by hand.

## Artifacts

| Artifact | Location | Committed? |
| --- | --- | --- |
| Config, phrasebank, code, this report | `experiments/target-language-speech-spans/` | Yes |
| Extracted audio, synthetic audio and truth | `data/experiments/target-language-speech-spans/` | No — derived media |
| Per-chunk detector outputs, Whisper transcripts, sweep, spans, full report | `data/experiments/target-language-speech-spans/<run-id>/` | No — contains third-party transcripts |
| Aggregate metrics (no transcript text) | `results.json` | Yes |
| Review worksheet (frozen units) | `<run-id>/review/worksheet.json` | No |
| **Language labels** (unit times, labels, reviewer, VAD settings, prepared-audio sha256) | `labels/<run-id>.language-labels.json` | **Yes** — complete (735/735 units); reusable ground truth, contains no audio or transcript |
| Caption proxy tracks | `data/experiments/target-language-speech-spans/captions/` | No — third-party captions |
