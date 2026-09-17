# Target-language speech spans in mixed-language audio

**Status:** Synthetic evaluation complete; real-clip human labels pending. Started 2026-09-14.
Provisional decision below; not promotable until labels, an impostor set and DS923+ costs exist.

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

- **Real clips** (`config-v1.json`): two zh and two es language lessons taught in English, ~42 min.
  Labelled blind by the owner through `review-serve` (audio only, per VAD unit: target / other /
  mixed / no speech / unsure), saved on every key press to the committed label store.
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

### Real clips — without human labels (labels pending)

The selected operating point applied unchanged:

| Clip | Target | Accepted spans | Accepted seconds | Evidence available now |
| --- | --- | --- | --- | --- |
| `TUJBWALllbo` | zh | 33 | 166 | Caption proxy: span precision 30/33 = 0.91, time precision 0.96, time recall 0.76 |
| `IniuZsvMTBM` | zh | 26 | 85 | Transcript reading only |
| `RubvgfZEVus` | es | 27 | 81 | Transcript reading only |
| `xglEjH0Ue8o` | es | 66 | 186 | Transcript reading only |

**Transcript reading, not listening.** I read the Whisper target-forced transcript of every accepted
span (Chinese, Spanish, English). This is weaker than labels — a forced transcript can be a
translation — but every accepted span also passed Whisper's own language ID at ≥ 0.7 over the
catalogue languages.

- **zh**: 56 of 59 spans read as Chinese sentences, much of `TUJBWALllbo` being fast native speech from
  the drama and interview clips the lesson analyses (`我妈喜欢穿红色就给我也买了很多红色的衣服`). Three
  carry English: `認識你很高興, It was really nice meeting you!`, `認識 and 知道`, and the channel
  outro. The two caption-proxy "hard failures" (`你知道吗?`, `他明天也`) read as Chinese; the proxy
  marks them English because an English caption cue overlaps them.
- **es**: all 93 spans read as Spanish (one is a spoken count transcribed as digits `1, 2, 3 … 9`),
  almost all of them example sentences (`Ponte el abrigo para que no cojas un
  resfriado.`, `Estuve en Barcelona tres días.`). 6 of 66 on `xglEjH0Ue8o` include a one-word English
  gloss (`Menos less.`, `Después, after. En España…`, `Only. Solo me gusta el chocolate.`), which the
  80 % span criterion counts as correct but a learner would see.
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

## Decision (provisional, pending labels)

- **Adopt the shape:** Silero VAD → VoxLingua on 2 s windows with a switch-penalised Viterbi →
  per-run gate requiring **both** VoxLingua (closed over the catalogue languages) **and** Whisper
  language ID ≥ 0.7, minimum 1 s → Whisper transcript of accepted runs only.
- **Do not use whole-chunk decisions, whole-file multilingual Whisper, or forced-language Whisper as a
  language filter.**
- **Not yet promotable.** The synthetic set is too easy to separate methods, the only real precision
  number is a biased caption proxy on one clip, and nothing has been measured on the DS923+. The
  owner's labels on the real clips decide the precision claim; a Catalan/Galician/Portuguese impostor
  set decides whether the closed set is safe.

## Limitations

- **Synthetic truth is easy.** Clean TTS, one speaker, and a pause of ≥ 60 ms at every switch. Every
  pair scores ~1.0, so it validates plumbing and length effects but cannot rank methods or confusable
  pairs. Real lessons glue glosses to examples without pauses.
- **Selection on synthetic, amended once** (see above); the real clips were looked at before the
  amendment.
- **No human labels yet**; real-clip claims rest on transcript reading and one biased caption proxy.
- **Catalan is untested as an impostor.** Chatterbox cannot speak Catalan; `voxset` removes Catalan
  from the competitors by construction, so real Catalan speech in a Spanish catalogue would pass the
  VoxLingua half of the gate and rely on Whisper's language ID alone.
- **The 80 % span criterion counts one-word English glosses as correct.**
- **Four real clips from two lesson channels per language, two target languages.** No street
  speech, music, overlapping speakers or other target languages.
- **Costs are M1 numbers**, partly inflated (redundant decoding) and partly unmeasured (DS923+).

## Next round

1. Owner labels (`review-serve`), then `report` for real precision and recall with denominators.
2. Impostor set from public labelled speech (FLEURS or Common Voice: ca, gl, pt, it for es; en for
   all) to measure false acceptance per method.
3. Two CTC verifiers on accepted spans: forced-alignment confidence of the Whisper transcript with a
   target-language CTC model (the repository's aligner already rejects low confidence), and
   character-error agreement between Whisper and an independent CTC transcript (MMS adapters or
   Omnilingual CTC 300M).
4. A stricter span criterion (e.g. no other-language unit at all) alongside the 80 % one.
5. Measure the selected pipeline on the DS923+.

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
$R decide && $R report
$R review-export
$R review-serve   # http://127.0.0.1:8765/ — every label is written to labels/ immediately
$R report
# Offline alternative: $R review-html, then review-import --worksheet <downloaded file>
```

Run heavy stages one at a time; every stage skips work already on disk.

## Artifacts

| Artifact | Location | Committed? |
| --- | --- | --- |
| Config, phrasebank, code, this report | `experiments/target-language-speech-spans/` | Yes |
| Extracted audio, synthetic audio and truth | `data/experiments/target-language-speech-spans/` | No — derived media |
| Per-chunk detector outputs, Whisper transcripts, sweep, spans, full report | `data/experiments/target-language-speech-spans/<run-id>/` | No — contains third-party transcripts |
| Aggregate metrics (no transcript text) | `results.json` | Yes |
| Review worksheet (frozen units) | `<run-id>/review/worksheet.json` | No |
| **Language labels** (unit times, labels, reviewer, VAD settings, prepared-audio sha256) | `labels/<run-id>.language-labels.json` | **Yes** — reusable ground truth; contains no audio or transcript |
| Caption proxy tracks | `data/experiments/target-language-speech-spans/captions/` | No — third-party captions |
