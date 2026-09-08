# Plan 17: Passage extraction methods and side-by-side evaluation

**Status:** Planned

**Depends on:** Plans 02 and 03. Uses Plan 09's audio cache, which is authorized below. Independent
of Plans 10, 11 and 16; its measured per-arm costs are an input to Plan 16's resource caps.

## Outcome

Compare several passage-extraction methods against the current heuristic under blind human
side-by-side judgment over the same fixed occurrences, and promote a winner only where held-out
human preference supports it. Produce a dated experiment report, a reusable strategy interface, and
a documented promotion gate that permits a different method per language.

A negative result — "the current heuristic is already the best of these, and here is the measured
margin" — is a valid and useful outcome.

## Current state

The passage a learner reads is produced by exactly one un-tuned heuristic, written early and never
compared against an alternative. `segment_payload` (`captions.py:203-259`) accumulates caption units
and closes a group at the first of: terminal punctuation, end of track, an inter-unit gap of
`0.9 s` or more, or `>= 15 s` / `>= 32` tokens. `_merge_short_segments` (`captions.py:163-200`)
then merges groups under four tokens forward, then backward.

The passages are usually good, but "usually" is an anecdote and the failure modes are invisible
without a comparison. Reading the code surfaces concrete defects, several of them outright bugs:

- **`TERMINAL_RE` is Latin-only** (`text.py:8`): `[.!?…]` never matches `。！？`, the danda `।`, `؟`,
  or the Greek `;`. **No Japanese or Chinese segment can ever have
  `boundary_reason == "punctuation"`.**
- **A trailing `…` is treated as the strongest possible boundary when it usually means the
  opposite.** In authored subtitles a line-final ellipsis is a *continuation* marker. It fires
  `reason="punctuation"` with `confidence=1.0`, and the `reason != "punctuation"` guards at
  `captions.py:176` and `:187` then make that split unmergeable.
- **`TOKEN_RE` silently breaks three other rules for scriptio continua.** `[^\W_]+` (`text.py:7`)
  matches an entire run of Han characters as one token, so `hard_tokens = 32` is unreachable, the
  `count < 4` merge test almost always fires, `length_score` (`captions.py:118`) never reaches 1.0,
  and `Segment.token_count` — used elsewhere as an eligibility filter — is meaningless.
- **`join_text` corrupts CJK text** (`text.py:57-66`) by inserting ASCII spaces between word-level
  units. That corrupted text is what gets stored, displayed **and analyzed**.
- **`join_text` also glues across speaker turns**: its `left[-1] in "¿¡[({—-/"` branch joins a
  trailing `—`/`-` — a speaker-turn marker in authored captions — to the next speaker's line with no
  space at all.
- **One pause constant spans two incompatible timing sources.** `pause_seconds = 0.9` applies
  identically to authored cue gaps (inflated by display timing, not speech) and to automatic word
  gaps, where `end` is itself synthesised as `min(unit.end, start + 0.4)` (`captions.py:63`) —
  making the "gap" a function of speech rate. `pause_seconds`, `hard_seconds` and `hard_tokens` are
  function defaults with no `Settings` field and no caller that overrides them.
- **YouTube automatic captions carry no punctuation at all**, so on automatic tracks the punctuation
  rule is dead and every boundary comes from a pause, a hard cap, or end-of-track.
- **Stanza already computes sentence boundaries and they are discarded**: `analysis.py:243-282`
  iterates `document.sentences` purely to reach tokens.
- **There is no notion of neighbouring context**: no `prev_segment_id`, no context query, no API
  field. `_merge_short_segments` also accepts `video_id` and `video_duration` and uses neither.

`docs/design.md` already states the intent — *"Better boundaries are a progression … The baseline
deliberately stops at the first two."* This plan takes the next steps and measures them.

### Scope boundary against the neighbouring plans

| Question | Plan |
| --- | --- |
| **Which** occurrence should we show? (ranking, diversity) | 11, 12 |
| **Where does the shown passage start and end?** | **this plan** |
| How precise are the *timestamps* of that passage? | 10 |
| Is the caption *text itself* correct? | 09 |

The comparison unit holds the occurrence fixed and varies only the span, so this work is orthogonal
to ranking and runs before Plan 11 exists.

### Standing authorization for local audio

Audio acquisition and local retention are permanently authorized for this project. Plan 09's framing
— *"pending an operator authorization decision"* — is more restrictive than the owner's actual
position and blocks useful work. Part of this plan is to reword those terms in
[`09-audio-and-caption-reliability.md`](09-audio-and-caption-reliability.md), its implementation
notes, and any README or `.env.example` text presenting audio as awaiting permission. Audio stays
off by default in the published quick start; that is a distribution default, not a permission gate.

### Assumption on catalogues

Only `config/channels/es.json` exists today. Catalogues for the remaining ten target languages will
be added before implementation begins, with similar volume and structure to the Spanish one. No
eval-only throwaway catalogues are created. Every round names the catalogues it needs, so a missing
one fails loudly at `prepare` rather than silently shrinking a stratum.

## Decisions

### Constraint on candidate methods

Every promotable arm runs offline, CPU-only and pinned.

**Latency is not a selection criterion.** Segmentation runs once at indexing time, never on the
query path — where latency does matter and where nothing here applies. A DS923+ indexing
continuously processes roughly 24 hours of video per day at RTF 1.0, comfortably above the ingestion
rate of a small curated catalogue. Treat **RTF ≤ ~1 as acceptable** and **RTF ≤ ~4 as tolerable for
a one-off backfill**, and select on quality rather than speed. This deliberately reopens larger
models that a latency-first reading would have excluded.

**Primary target — Synology DS923+, 20 GB RAM.** AMD Ryzen R1600, 2 cores / 4 threads, AVX2 but no
AVX-512/VNNI. With 20 GB installed, neither RAM nor model size is binding; a 1–2 GB model is
reasonable. Use fp32 or `quint8_avx2` ONNX and never `qint8_avx512_vnni` variants, which this CPU
cannot accelerate, and set `intra_op_num_threads=2` explicitly so ONNX Runtime does not starve DSM
on a two-core box.

**Secondary, tentative — a Raspberry Pi 4/5 class board with 4 GB.** aarch64/NEON rather than
x86/AVX2, so the quantized build differs and DS923+ timings do not transfer; here RAM *is* binding,
with a working set above roughly 2 GB out of reach. This is an aspiration, not a gate. Where a large
arm has a smaller sibling, the sibling is measured as the Pi-viable fallback rather than becoming a
separate blind card.

Each arm records model size, peak RSS, and seconds per hour of captions **measured on the DS923+**,
plus a `pi_viable` field of `yes` / `no` / `untested` derived from peak RSS and architecture. Never
report a Pi number that was not measured on a Pi. An arm with no DS923+ measurement cannot be
promoted. These measurements are the natural input to Plan 16's resource caps.

One **non-promotable ceiling arm** (Gemini) is judged blind alongside the rest, marked
`promotable: false` and excluded from the promotion gate by construction. It exists so a result can
distinguish "the heuristic is near the achievable limit" from "all cheap methods are equally
mediocre".

### Target languages, and per-language winners are allowed

The target set is **English, Spanish, French, German, Japanese, Korean, Italian, Hindi, Chinese,
Portuguese, and Russian**.

**A single global winner is not required.** Where a method wins distinctively for one language, that
language uses it. This is a design requirement: the strategy configuration is a **per-language map
with a default**, and the promotion gate is evaluated per language. It is also the likely outcome,
since the arms differ mainly on unpunctuated and scriptio-continua input.

Coverage across the eleven: A1 and A2 cover all of them (Stanza has models for each), A3/A4 cover
all (SaT spans 85 languages), A6 covers all. A5's family claims 47 languages including Spanish,
English, Russian, Japanese, Korean and Chinese — **verify Hindi, Italian and Portuguese at
implementation time** and record the answer. An arm is simply unavailable for a language it does not
cover, which the per-language map handles naturally.

To guard against selecting noise, a per-language winner is promoted only on a large margin, stated
as an explicit effect-size threshold in the frozen config **before** results are examined.

### The arms

Selection principle: **no two arms may be provably identical on the same input**, which collapses
the entire rule-based-splitter family into one heuristic arm. Because latency is relaxed, each
family is represented by its best member rather than its fastest; the cheaper sibling is measured
for the cost table and the Pi target instead of consuming a card.

| # | Arm | Model | Cost on DS923+ | Why it is not redundant |
| --- | --- | --- | --- | --- |
| A0 | `heuristic-v1` — current production, unchanged | none | 0 | **The control.** Without it no improvement is attributable. |
| A1 | `heuristic-v2` — the heuristic work below | none | 0 | The only arm that fixes CJK *text correctness*, not just boundaries. Likely the largest win at zero deployment cost. |
| A2 | `stanza-ssplit` — Stanza's own sentence boundaries | ~0.7 MB/lang, already a dep | negligible | Represents the whole punctuation-driven family. Its predicted degeneracy on unpunctuated tracks is a result worth recording. |
| A3 | `sat-text` — SaT `sat-12l-sm`, text only, timing ignored | 559 MB ONNX | ~2–4 min/hour | Pure-text state of the art. `sat-3l-sm` (428 MB, ~30 s/hour) is measured as the Pi-viable fallback, not carded. |
| A4 | `sat-fused` — SaT `predict_proba` per-character boundary probability fused with the pause signal and A1's caps | same | same | The only arm using both signals jointly, and the most likely winner. Genuinely different output from A3. |
| A5 | `punct-restore` — `xlm-roberta_punctuation_fullstop_truecase`, boundaries mapped back to word indices, original text displayed | 1.11 GB ONNX | minutes/hour | Punctuation, truecasing and sentence boundaries in one pass over lowercase unpunctuated input; 47 languages, Apache-2.0. `punct_cap_seg_47_language` (233 MB) is the measured Pi fallback. |
| A5d | `punct-restore-display` — same boundaries, restored punctuated text **shown to the learner** | same | same | A different user-visible artifact, not just different boundaries. Automatic tracks only; collapses into A5 on authored tracks. |
| A6 | `asr-resegment` — faster-whisper `large-v3-turbo` int8 | ~2.5 GB RAM | RTF ~2–4 | A genuine candidate under a relaxed indexing budget, not merely a reference. See the caveat below. |
| A7 | `gemini-ceiling` | frontier API | — | **Non-promotable.** Headroom only. |
| A8 | `local-llm` — Qwen3-4B-Instruct Q4_K_M via llama.cpp, constrained boundary-index output | ~2.5 GB | RTF ~0.5–1 | Also a genuine candidate now. Predicted loss, but cheap enough to settle rather than leave open. |

**A6 changes the displayed text, which is a product decision beyond this plan.** Whisper re-derives
the transcript rather than segmenting captions, so it cannot be scored on the same character
offsets, and adopting it would mean showing ASR output instead of caption text — exactly the trust
question Plan 09 exists to answer, and mapping it back onto caption text needs Plan 10's alignment.
Judge it blind like any other card; if it wins, the report states what adopting it would require
rather than treating it as a drop-in.

**A8 is predicted to lose.** The SaT paper evaluated Llama-3-8B and Command R on this exact task and
both lost to a 3-layer 0.2B encoder in every language, while altering 1.5–2% of input characters
despite explicit instructions not to. Constrain the output to integer boundary indices over a
windowed transcript so the model cannot rewrite text at all. Qwen3-4B is Apache-2.0 and is a small
local model, not a frontier system, so it stays inside the stated constraint.

**Explicitly excluded, with the reason recorded in the config** so the choice is auditable:

- pySBD (0.3.4, 2021, dormant), NLTK punkt (**no ja/zh/ko**), blingfire (0.1.8, 2021), syntok
  (Indo-European focus), and the spaCy `sentencizer` (weaker than a corrected regex) — all
  punctuation-driven, all produce one giant segment on automatic captions, all redundant with A1.
- `oliverguhr/fullstop-punctuation-multilang-large` and `kredor/punctuate-all` — excluded on
  **language coverage, not size**: `en/de/fr/it(/nl)` only, so no Spanish, Russian, Hindi, Korean or
  CJK. A5 covers 47 languages at the same order of magnitude.
- NVIDIA NeMo punctuation — English-only checkpoints and requires the full `nemo_toolkit`; its
  multilingual value already *is* A5, which ships the same model family pre-exported to ONNX.
- `sat-1l-sm` — 399 MB against `sat-3l-sm`'s 428 MB, since the XLM-R embedding matrix dominates, so
  it saves nothing over the fallback A3 already measures.
- All embedding models (`multilingual-e5-small`, `paraphrase-multilingual-MiniLM`, LaBSE) — semantic
  chunking operates over *already-segmented* sentences to find topic boundaries and cannot propose a
  boundary inside an unsegmented word stream. Wrong granularity at any latency budget. Keep
  `multilingual-e5-small` (118 MB int8 `quint8_avx2`) in mind for a later near-duplicate step.
- `distil-large-v3` — English-only.

**Two integration facts shape the work.** The heuristic, Stanza and SaT arms are lossless over
characters, so boundary offsets map straight back onto `TimedUnit`s. A5 **rewrites the text**, so
the word-to-timestamp mapping must be rebuilt: run ONNX and SentencePiece directly rather than
through the stale `punctuators` wrapper (0.0.7, 2024, which hard-depends on torch for pure-ONNX
inference), keeping the subtoken-to-word alignment so boundaries land on word indices. That
alignment is the main cost of A5 and is not optional. A6 produces its own timeline entirely.

### Heuristic work (arm A1, promotable on its own)

Each item is separately testable and needs no model:

1. **Terminators.** Hardcode the terminal class — CJK `。！？．‼⁇⁈⁉`, danda `।॥`, Arabic `؟۔`, Greek
   `;` (U+037E and ASCII), Armenian `։`, Ethiopic `።`, Tibetan `།` — plus CJK closing marks
   `」』）】》〉〕`. Hardcode rather than adding a `regex` dependency for `\p{Sentence_Terminal}`:
   deterministic, dependency-free, and pinned for reproducibility.
2. **Demote `…`** to its own low-confidence reason, or drop it from the terminal class. Test both.
   Guard against abbreviation and decimal false positives (`Sr.`, `p. ej.`, `e.g.`, a lone initial,
   digits either side of a period).
3. **Script-aware token counting**: each Han/kana/Thai/Lao/Khmer codepoint counts about one (Han
   1.0, kana ~0.5); spaced scripts unchanged; Hangul stays on the word rule, since Korean uses
   spaces.
4. **No-space join** when both sides are in a no-space script, plus full-width punctuation in
   `SPACE_BEFORE_PUNCTUATION_RE` and `SPACE_AFTER_OPEN_RE`.
5. **Speaker turns**: leading `-`/`—`, `>>`, and `NAME:` become hard boundaries, and stop gluing
   across them. Generalize `ANNOTATION_RE` from its hardcoded Spanish word list to bracketed spans
   plus `♪`/`♫` — lyrics are arguably an exclusion, not merely a boundary, for a learning corpus.
6. **Pause threshold**: separate values for manual and automatic tracks, and prefer a **per-video
   percentile of inter-onset gaps (p90–p95) over an absolute constant** — self-calibrating, with no
   per-language config table. Expose the 0.4 s word-duration cap, which directly determines the gap
   signal.
7. Remove the dead `video_id` and `video_duration` parameters from `_merge_short_segments`.

**Discourse-marker trimming** (`bueno`, `pues`, `o sea`, `well`, `um`, `ну`, `вот`, `えーと`, `那个`)
belongs at **display time, not index time**: for a learner, fillers are authentic spoken usage and
must stay in the retrieval index.

### Context expansion is a factor, not an arm

"When the match sits within N characters of a boundary, or the segment's `boundary_reason` is
`forced` or `end`, merge with the neighbour before rendering and clipping" applies on top of any
method and is the cheapest fix for the most visible failure of all of them. Evaluate it as a small
factorial — A0 and the best base arm, each with and without expansion — in a second round, rather
than doubling the arm count in the first. It needs a stable previous/next link between adjacent
segments, or re-lookup by `(video_id, track_id, start)`.

### The comparison unit is an occurrence, not a segment

This is the load-bearing contract:

- An **occurrence** is `(video_key, track_id, query, anchor)`, where the anchor is a character and
  time span in the *concatenated cleaned unit stream*, computed before any segmentation and
  therefore method-independent. Every arm answers "give me the passage covering this anchor".
- An arm returning nothing records `no_passage`; one returning several records `ambiguous`. Neither
  is silently repaired.
- Arms producing byte-identical text **collapse into one card**, with the producing arm set
  recorded. This saves a large share of reviewer effort and makes divergence measurable.
- The playback range is the **union** of every arm's `[clip_start, clip_end]`, so no arm's audio is
  clipped and playback cannot favour one.

### Queries and sampling

Queries are drawn from the corpus's own `ngram_stats` in seeded, predeclared frequency bands —
common, mid and rare single words plus 2–5-word phrases — with bands and seed recorded in the frozen
config. No hand-curated query list.

Occurrences are sampled in **two strata**, and the distinction matters for honest reporting:

- **Representative stratum** — an unconditioned seeded draw. This is the only stratum from which
  corpus-level acceptability and divergence rates may be reported.
- **Disagreement-enriched stratum** — sampled only from cells where two or more arms disagree,
  stratified by `language × caption_kind`. This is where reviewer time buys information; the
  informative cells are `es/automatic`, `ru/automatic`, `ja/automatic` and `zh/automatic`.

Never pool the two without reweighting, and state in the report which stratum every number came
from. Report per-cell inter-arm agreement as a first-class result rather than assuming the
"redundant on authored captions" hypothesis. Reuse the frozen-config, stable-`sha256`-ordering,
per-video-cap sampling pattern from `experiments/audio-caption-reliability/caption_reliability.py`
(`review_subset`), and predeclare the subset before any output is examined.

### What the reviewer does — a blind N-way card grid

One occurrence per screen; every distinct passage is a shuffled, unlabeled card under a seeded
permutation stored in the worksheet.

1. **Play the clip**, then read the passages. Audio is base64-embedded from the Plan 09 cache over
   the union range, so the page stays offline and the judgment is made against speech, not text.
2. **Pick the single best card** (required).
3. **Mark every card acceptable to show a learner** (multi-select) — the product-relevant absolute
   number alongside the preference signal.
4. **Per-card defect flags** from a frozen vocabulary, each with a behavioural anchor over 30
   characters: `cut_before_start`, `cut_after_end`, `over_reach`, `too_long`, `too_short`,
   `dangling_opener`, `context_needed`, `speaker_mixed`, `mangled_spacing`, `annotation_noise`,
   `duplicated_text`, `restoration_error` (displayed punctuation or casing is model-invented and
   wrong — reachable only for A5d), and `target_missing` (the queried expression is absent, a hard
   failure).
5. An optional note; a reviewer name is required before export.

**Blinding.** Arm identity, arm provenance, the cue-gap timeline, and boundary reasons are withheld
until the best-card choice is recorded, reusing the `gated[]` and `gate()` mechanism and its
locked-in tests from `experiments/audio-caption-reliability/review_app.py`. Seeing "this one came
from the pause rule" before judging would anchor the verdict exactly as the ASR reference would in
Plan 09.

### Languages and rounds

| Round | Languages | Size | Purpose |
| --- | --- | --- | --- |
| 1 · Pilot | `es` | ~40 occurrences | Validate the rubric and measure the card-collapse rate. Revise once, then freeze rubric v1. |
| 2 · Main | `es`, `en`, `ru` | ~120 occurrences each | **The headline evidence.** Stratified by caption kind, query band, and speech style. |
| 3 · Translation-mediated | `ja`, `zh`, `ko` | ~40 each | An English gloss per card. The scriptio-continua and no-punctuation cases, where the arms differ most. |
| 4 · Judge scale-out | `fr`, `de`, `it`, `pt`, `hi` | as needed | Calibrated LLM judge only, completing the eleven. |

Rounds 3 and 4 are reported separately and never merged into the headline.

### Translation-mediated rounds

Each card receives an independent Gemini English gloss under one fixed registered prompt, blind to
the arm, with the source text kept visible beside it. State the confound plainly: a truncated
passage produces a truncated gloss — which *is* the signal — but a translation error can be mistaken
for a boundary error. Round 3 therefore analyses only the flags that survive a gloss
(`cut_before_start`, `cut_after_end`, `over_reach`, `target_missing`, `mangled_spacing`) and
explicitly drops `context_needed`, `too_long`/`too_short`, and register judgments, which cannot be
assessed through a translation.

### LLM judge

A schema-constrained Gemini judge replicates the same N-way choice, calibrated against Rounds 1–3.
The report states per-language agreement (Krippendorff's alpha or Cohen's kappa) and the correlation
of judge win-rates with human win-rates **before** any judge-only language is reported. Judge rows
carry `label_source: "llm"` with prompt, model and provider version, matching Plan 11's policy that
human labels are the only headline evidence.

### Metrics

- **Acceptability rate** per arm — the primary number, since exactly one arm ships per language.
- **Win rate** per arm with bootstrap confidence intervals; collapsed cards credit every producing
  arm. Bradley-Terry scores from the implied pairwise comparisons as a secondary view.
- **Defect rate** per arm per flag.
- **Hard-failure rate**: `target_missing`, `no_passage`, `ambiguous`.
- **Divergence rate** by caption kind and language, from the representative stratum only.
- **Cost**: model size, peak RSS, and seconds per hour of captions on the DS923+, plus the
  `pi_viable` verdict. Quality and cost are reported side by side so a marginal quality win at
  RTF ~4 can be judged against a near-free alternative.
- **Corpus impact**: segment-count delta, mean tokens and duration, boundary-reason distribution.

### Promotion gate

The gate runs twice: once for the **global default**, and once **per language**.

A candidate becomes the global default only when it:

1. beats A0 on acceptability rate with a bootstrap confidence interval excluding zero on the
   held-out human set, in at least `es` and `en`;
2. increases no hard-failure rate;
3. fits the measured DS923+ budget — the Pi target informs the choice but does not gate it;
4. degrades honestly, falling back to the heuristic with a visible state when its model is absent,
   per the repository's standing contract.

A candidate becomes a **per-language override** only when it clears 2–4 above *and* beats the global
default for that language by more than the pre-registered effect-size threshold. Languages evidenced
only by the LLM judge in Round 4 may not receive an override at all, since judge labels are never
headline evidence.

Otherwise record the negative result and keep the baseline. **A1 is judged on the same gate but may
be promoted independently**, since it needs no model and fixes outright bugs.

## Implementation work

1. **Extract a two-pass `PassageStrategy` protocol** into a new `src/speech_retrieval/passages.py`,
   mirroring the `TextAnalyzer` protocol at `analysis.py:92-97`: take the **whole**
   `list[TimedUnit]` and return split indices plus reasons, keeping `_merge_short_segments` and
   `_segment_from_units` shared. The current streaming loop at `captions.py:222-241` decides one
   unit at a time, which no model arm can use — SaT alone wants blocks of at least 256 subtokens.
   Register the existing algorithm as `heuristic-v1`; default behaviour is unchanged and
   `tests/test_captions.py` must pass byte-for-byte.
2. **Ship A1 next.** It fixes correctness bugs that would otherwise contaminate every other arm's
   `token_count` and `char_start`/`char_end` on CJK.
3. **Add an anchor extraction helper** producing method-independent occurrence anchors over the
   concatenated unit stream, reusing `text.tokens_with_spans` and the query normalization in
   `search.py`, plus a `passage_for_anchor` accessor per strategy.
4. **Add A2** (nearly free), then **A3/A4** behind `wtpsplit-lite` rather than `wtpsplit`, which
   drags in torch and transformers for roughly 2.5 GB of install; `wtpsplit-lite` needs only
   `huggingface-hub`, `numpy`, `onnxruntime` and `tokenizers`. That is a convenience on the 20 GB
   NAS but decisive for the 4 GB Pi target. Measure `sat-3l-sm` alongside `sat-12l-sm` for the cost
   table. Then **A5/A5d**, which carry the subtoken-to-word alignment work, and finally **A6** and
   **A8**, which need no alignment but the most compute. All sit behind a new
   `segmentation-experiments` optional extra. Each arm records name, version, model id and parameter
   hash, and is skipped with a recorded reason for any language its model does not cover.
5. **Create `experiments/passage-extraction/`** in the Plan 04 and Plan 09 shape: a pure logic
   module plus a staged runner named `run_passages.py` — never `run.py`, since mypy treats duplicate
   module names across experiment directories as a conflict — a frozen `config.json`, a
   `result-schema-v1.json` generated from the Pydantic models with a drift test, and artifacts under
   the gitignored `data/experiments/passage-extraction/<run-id>/`. Stages:
   `prepare`, `extract`, `audio`, `gloss`, `review-export`, `review-html`, `review-import`,
   `judge`, `report`. The `audio` stage reuses the Plan 09 cache and `AudioClipRange` over the
   union range with `padding=0`, as existing callers do.
6. **Predeclare the sample** in both strata before any output is examined, with a
   `duplicate_review_fraction` for self-agreement and a frozen-sample test asserting rows are never
   silently swapped.
7. **Build the single-file offline review page**, modelled on
   `experiments/audio-caption-reliability/review_app.py`: a JSON payload in a
   `<script type="application/json">` escaped by the same `_script_json`, `localStorage` keyed by
   run id, export refusing to proceed without a reviewer name, keyboard operation (digits pick best,
   letters toggle acceptable, space replays, `n` advances), and audio embedded subject to the same
   total-size ceiling as `collect_audio`. Occurrences whose audio failed to prepare are judged on
   text with the missing audio stated on the card, never hidden.
8. **Add the gloss stage**: a new `PromptSpec` in `prompt_registry.py`, reusing
   `GeminiTranslationProvider` and the RPM pacer from
   `experiments/target-language-word-alignment/run_experiment.py`, with per-call checkpointing and a
   content-hash cache.
9. **Add the judge stage**, emitting the same worksheet schema with `label_source: "llm"`, plus the
   calibration report.
10. **Record the standing audio authorization** by rewording Plan 09 and the related README and
    `.env.example` text as described above.
11. **Publish the report and update the roadmap**: `experiments/passage-extraction/README.md` with
    the canonical headings (Coverage, Quality, Cost, Reproduction, Verification, Decision,
    Limitations) and an Artifacts table with a `Committed?` column; the `experiments/index.md` row
    added **only once evidence exists**; `docs/design.md`'s Segmentation section and this
    directory's index updated in the same change.

Promotion, if it happens, is a separate follow-up commit that bumps `DATABASE_SCHEMA_VERSION`
(`identity.py:6`) and records the strategy name and version in segment provenance. Note explicitly
that **changing the strategy changes every `segment_id`**, since it hashes start, end and text
(`identity.py:46-64`) — promotion is a full reindex, and the experiment itself never writes to the
production index.

## Public interfaces and data

- A `PassageStrategy` protocol and a name-to-strategy registry; a `Settings` field holding a
  **per-language strategy map with a default**, initially `{"*": "heuristic-v1"}`, resolved the way
  the analyzer cascade already resolves per language in `analysis.py:305-331`. `pause_seconds`,
  `hard_seconds` and `hard_tokens` become real configuration instead of unreachable function
  defaults. Recorded segment provenance names the strategy that actually ran, so a mixed-strategy
  corpus is reproducible — mirroring how `meta` already records `analyzer_id: "mixed"`.
- Versioned Pydantic records with `extra="forbid"`: `PassageCard` (arm set, text, timing, flags),
  `OccurrenceRow` (anchor, arms, cards, shuffle seed, stratum), and `ComparisonVerdict` (best card,
  acceptable set, per-card flags, reviewer, rubric version, `label_source`). JSON Schemas are
  generated from the models and drift-tested.
- No change to `/api/v1` and none to the React player in this plan.

## Verification

- `uv run pytest`, including new tests: `heuristic-v1` reproduces current segments byte-for-byte
  over the existing caption fixtures; anchors are identical across arms; identical texts collapse to
  one card; the blinding gate strings are present, using the same assertion idiom as
  `tests/test_audio_caption_reliability_experiment.py`; every flag has an anchor over 30 characters
  and the UI option lists match the `Literal[...]` members; worksheet export and import round-trip;
  the rendered page contains no `http://` or `https://`; and direct assertions on each A1 fix — CJK
  terminators, script-aware token counts, no-space joins, speaker-turn splitting, and the `…`
  demotion.
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv build`.
- Confirm `speech-retrieval reindex` output is unchanged after step 1 — same segment count, same
  segment IDs — **before** any arm work begins.
- Run the pilot end to end on the Spanish cache: `prepare`, `extract`, `audio`, `review-export`,
  `review-html`, open `review.html` from disk, label 40 occurrences, `review-import`, `report`.

## Risks and limitations

- **Single annotator.** The reviewer is the author; mitigate with a duplicate-review fraction
  measuring self-agreement over time, and report it rather than hiding it.
- **Preference is not usefulness.** A passage can read better yet play worse, which is why every
  round is judged after listening rather than from text alone.
- **The disagreement-enriched stratum is not the corpus.** Corpus-level rates come only from the
  representative stratum; pooling without reweighting would inflate every difference.
- **Translation-mediated judgment is weaker evidence**, reported separately with a restricted flag
  set.
- **Per-language selection multiplies comparisons.** Eleven languages against nine arms will produce
  an apparent per-language winner by chance alone. The pre-registered effect-size threshold and the
  ban on overrides for judge-only languages absorb that, and the report states how many per-language
  comparisons were made.
- **The ceiling arm is a temptation.** It will probably look best. The config marks it
  `promotable: false` and the gate cannot see it, but the report must also say plainly that it is
  not a shippable option under this project's constraints.
- **Authored tracks may show near-zero divergence**, in which case the honest finding is that this
  work matters only for automatic captions and CJK — and the pilot should surface that before the
  main rounds are collected.
