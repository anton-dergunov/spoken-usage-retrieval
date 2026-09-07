# Caption reliability against a recorded ASR reference

**Status: not run. The tooling, frozen configuration, and preflight are complete and verified; no
empirical result exists yet.** This directory deliberately contains no `results.json`, and
[`experiments/index.md`](../index.md) deliberately has no row for this experiment. Nothing here
claims a measured finding.

Implements the benchmark half of
[Plan 09](../../docs/plans/09-audio-and-caption-reliability.md).

## Decision and hypothesis

**Decision.** For each predeclared source class, choose exactly one of `use_directly`,
`attach_score`, `verify_selectively`, `replace_with_asr`, or `collect_more_evidence`.

**Hypothesis.** Automatic Spanish captions disagree with a Whisper `large-v3`-class reference
materially more than authored Spanish captions, and that disagreement is concentrated in cases a
human agrees are real caption errors rather than in punctuation and segmentation differences.

**A negative result is a valid outcome.** If ASR disagreement does not reliably identify caption
errors, or if the acoustic features do not track human judgements, that is a completed experiment
provided the criteria below are met.

## Success criteria

Decision quality, not a universal WER cutoff:

- the frozen sample is rerunnable without silent substitution, and every stage failure is recorded
  against the same row;
- every predeclared source class has a disagreement distribution with **both** segment-level and
  video-level denominators;
- automatic disagreement is reported separately from human-confirmed caption error;
- every predeclared source class receives one recommendation, with cited distributions and reviewed
  examples;
- every optional acoustic feature is marked `usable`, `not_usable`, or `insufficient_evidence`
  against a predeclared human counterpart and an exact reviewed denominator.

## What is measured, and what it is not

The automatic metric is oriented `reference = normalized ASR`, `hypothesis = normalized caption`, so
the denominator is ASR tokens (or characters). It is named **`caption_asr_disagreement`**, never
"caption error rate". ASR is a *declared comparison reference*, not ground truth: an ASR model and an
automatic caption pipeline can share correlated weaknesses, and a disagreement can equally reflect a
caption error, an ASR error, a caption segmentation difference, or an audio window that excludes
speech because cue timing is poor. Only listening and manual adjudication converts a disagreement
into a confirmed caption error, and the rubric explicitly adjudicates the ASR baseline too
(`reference_assessment: equivalent | caption_better | asr_better | both_wrong | uncertain`).

Normalization preserves diacritics, digits, and filler words, because those differences are exactly
what the benchmark should expose. Accent folding exists only as a separately named sensitivity
analysis (`word-accent-folded-v1`). The retrieval normalizer in
[`src/speech_retrieval/text.py`](../../src/speech_retrieval/text.py) is deliberately *not* reused: it
strips accents.

The existing `quality_score` on a segment is not observed transcript reliability — it is a
deterministic mix of boundary confidence, duration, token count, and a manual-caption prior. It is
carried as a descriptive covariate so the experiment can test whether that prior is justified, and it
never feeds sampling or conclusions.

## Frozen configuration

[`config-v1.json`](config-v1.json), validated against [`config-schema-v1.json`](config-schema-v1.json)
(generated from the Pydantic models in [`caption_reliability.py`](caption_reliability.py); a
regression test fails if the committed schema drifts).

| Setting | Value |
| --- | --- |
| Predeclared source classes | `es/authored`, `es/automatic` |
| Requested per class | 24 segments (48 total) |
| Seed / ordering | `20260907`, stable `sha256("<seed>\0<segment_id>")` ordering |
| Eligibility | ≥ 4 tokens, clip 1.5–15.0 s, non-empty text, valid source cache |
| Per-video cap | 8 segments; ≥ 5.0 s gap between selected clips in one video |
| Clip range | the segment's existing `clip_start`/`clip_end`, `padding=0` (no second padding layer) |
| Preparation | `pcm-s16le-mono-16000-v1` |
| Reference | faster-whisper `large-v3`, Spanish forced, beam 5, best-of 5, no VAD filter, no conditioning on previous text, **no initial prompt**, word timestamps on |
| Scoring | `word-v1` for Spanish; sensitivity `word-accent-folded-v1` |
| Review | `caption-review-v1`; all failures plus 5 per disagreement bin per class, bins `[0,0.1) [0.1,0.3) [0.3,∞)` |

The reference run must never prime the model with the caption under test; `initial_prompt` is typed
as `None` so a future edit cannot quietly bias agreement upward. faster-whisper's decoding defaults
differ from OpenAI Whisper's, so this is reported as a `large-v3`-*class* reference implementation
with its exact settings recorded, not as backend-equivalent output.

## Preflight evidence (real, 2026-09-07)

`preflight` was run against the actual local cache. This is the only measurement this document
currently reports.

Sampling funnel over the indexed Spanish corpus:

| Step | Count |
| --- | --- |
| Candidate indexed segments | 5,213 |
| Rejected: fewer than 4 tokens | 1,440 |
| Rejected: clip shorter than 1.5 s | 4 |
| Rejected: clip longer than 15.0 s | 11 |
| Eligible | 3,758 |
| Rejected during selection: per-video cap | 9 |
| Rejected during selection: overlapping neighbour | 4 |
| Selected | 48 |

Achieved strata:

| Class | Requested | Eligible segments | Eligible videos | Selected | Selected videos | Missing |
| --- | --- | --- | --- | --- | --- | --- |
| `es/authored` | 24 | 298 | 3 | 24 | 3 | 0 |
| `es/automatic` | 24 | 3,460 | 10 | 24 | 9 | 0 |

Environment and blockers:

- ffmpeg 9.0.1 and ffprobe 9.0.1 present;
- `faster_whisper`, `silero_vad`, `torch`, `torchaudio`, `jiwer`: **not installed**;
- audio cache: 13 videos in the caption cache, **0 ready**, 13 missing, 0 failed, 0 bytes;
- `authorization.confirmed` is **false**.

`preflight` therefore exits nonzero, and `prepare`/`transcribe` refuse to run.

**The dominant known limitation is already visible here: three independent authored videos.** Twenty
four authored segments drawn from three videos are not twenty four independent observations of
authored-caption quality. Unless acquisition is expanded, the honest ceiling for the authored class
is `collect_more_evidence`, and the report must present video-level denominators next to every
segment-level number rather than reporting means over correlated segments.

## Authorization gate

`authorization.confirmed` is `false` in the committed configuration and the runner refuses to
download media or transcribe until an operator sets it **together with a non-empty `basis`** —
confirming without recording why is rejected, because the report has to be able to state the grounds
on which these sources were downloaded and retained. This is not
boilerplate. YouTube's [Terms of Service](https://www.youtube.com/t/terms) restrict downloading and
automated access except as the service permits or with prior permission, and the
[API developer policies](https://developers.google.com/youtube/terms/developer-policies) prohibit
caching audiovisual content without prior written approval. Neither source determines whether a
particular local research use has a statutory exception. The operator must assess jurisdiction,
applicable terms, per-video copyright and licence, any research or teaching exception, and any
channel-owner permission, and may restrict the run to an allowlist via
`authorization.allowlist_path`. Downloaded audio, full transcripts, signed URLs, and model caches are
never committed or republished.

## How to run it

```bash
uv sync --locked --extra audio-experiments
export SPEECH_RETRIEVAL_WITH_AUDIO=true

# 0. Confirm the legal basis in config-v1.json (authorization.confirmed / basis), then:
uv run speech-retrieval doctor --json
uv run python experiments/audio-caption-reliability/run_reliability.py preflight

# 1. Acquire audio for the already-cached caption videos.
uv run speech-retrieval update --once --with-audio
uv run speech-retrieval audio-cache status --json

# 2. Freeze the sample, then run each stage. Every stage checkpoints after each item.
uv run python experiments/audio-caption-reliability/run_reliability.py sample    --run-id pilot-1
uv run python experiments/audio-caption-reliability/run_reliability.py prepare   --run-id pilot-1
uv run python experiments/audio-caption-reliability/run_reliability.py transcribe --run-id pilot-1
uv run python experiments/audio-caption-reliability/run_reliability.py score     --run-id pilot-1
uv run python experiments/audio-caption-reliability/run_reliability.py features  --run-id pilot-1

# 3. Listen to the predeclared review subset in a local page, then import the judgements.
uv run python experiments/audio-caption-reliability/run_reliability.py review-export --run-id pilot-1
uv run python experiments/audio-caption-reliability/run_reliability.py review-html   --run-id pilot-1
open data/experiments/audio-caption-reliability/pilot-1/review.html
uv run python experiments/audio-caption-reliability/run_reliability.py review-import --run-id pilot-1 \
    --worksheet ~/Downloads/review-worksheet.filled.json
uv run python experiments/audio-caption-reliability/run_reliability.py report --run-id pilot-1
```

`sample` refuses to overwrite an existing frozen sample. When media or a model is unavailable the row
keeps its identity and records `missing_audio`, `clip_failed`, or `asr_failed`; it is never replaced
by a different segment. `--retry-failed` re-attempts only the failed rows.

## Reviewing

`review-html` renders the exported worksheet as a single standalone page, with every prepared clip
embedded as a data URI, so it opens from disk with no server and no network. Judgements are held in
browser local storage and exported as `review-worksheet.filled.json` for `review-import`.

The page enforces the order the rubric assumes. The ASR text, the ASR-comparison question, and the
automatic disagreement rate are all withheld until the caption verdict is recorded, so the first
judgement is made against the audio alone and cannot be anchored by a reference that is not itself
truth. For the same reason acoustic tags must come from listening: the voice-activity and quality
features are evaluated *against* those tags, so reading the features first would make their
evaluation circular.

Rows in the subset with no prepared clip are pipeline gaps, not review items; the page marks them
non-reviewable and `review-import` ignores rows with no verdict.

## Artifacts

| Path | Committed? | Contents |
| --- | --- | --- |
| `config-v1.json`, `config-schema-v1.json`, `result-schema-v1.json` | yes | frozen configuration and validated schemas |
| `caption_reliability.py`, `run_reliability.py`, `review_app.py` | yes | sampling, scoring aggregation, stage runner, review page |
| `results.json` | on completion | run manifest, aggregates, denominators, policies — **no caption or ASR text** |
| `data/experiments/audio-caption-reliability/<run-id>/` | no (gitignored) | `sample.json(l)`, `clips.jsonl`, `asr.jsonl`, `scored.jsonl`, `features.jsonl`, `reviewed.jsonl`, `review-worksheet.json`, `review.html` |
| `data/raw/.../audio/`, `data/derived/audio/clips/` | no (gitignored) | source audio and derived clips |

Per-item rows retain caption text, ASR text, ASR segment and word timings with their diagnostic
fields, the normalized strings, the full alignment, every feature envelope, and any review record, so
an aggregate can be audited back to the audio it came from. Regenerate them with the commands above;
they are intentionally not published.

## What must still be reported before this is complete

Left blank on purpose — writing these before the run would be fabrication.

- distributions (median, IQR, p90, threshold counts) per source class, per channel, and per acoustic
  tag, with segment-, video-, and channel-level denominators;
- representative successful, borderline, and failed examples with their normalized and aligned
  intermediate output;
- the review rubric as actually applied: who reviewed, how many of how many, duplicate-review
  agreement, and concrete observations;
- one recommendation per source class with its operational consequence;
- `usable` / `not_usable` / `insufficient_evidence` for caption-ASR agreement, speaking rate, speech
  ratio, and each SQUIM output separately, each against its predeclared human counterpart
  (agreement vs. human-confirmed incorrect captions; rate vs. `fast_speech`; speech ratio vs.
  reviewed non-speech, boundary, and overlap cases; SQUIM vs. `unclear_speech` and
  `noise_or_music`);
- SQUIM subjective MOS is configured off: it needs a fixed, appropriately licensed non-matching
  speech reference, which does not exist here. Without one it stays `unavailable` rather than
  invented;
- measured disk use: bytes per minute of acquired audio, PCM clip seconds and bytes, model-cache size
  counted separately from corpus audio, and a projection for the configured corpus with its
  assumptions.
