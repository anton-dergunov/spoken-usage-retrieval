# Fixed-token translation and word-alignment experiment

## Decision

This experiment replaces the earlier one-call, model-invented chunk representation with two cached
operations: translate the complete sentence, then align immutable lexical token IDs. The production
representation is a many-to-many bipartite graph. Source and target character ranges are computed
locally; caption or forced-alignment timing is only a later projection onto source nodes.

The selected source-indexed adjacency prompt passed the preregistered numerical gates on the locked
held-out split: 39/40 structurally valid outputs (97.5%) and end-to-end micro F1 0.879 when the one
invalid output is scored as empty. Conditional on valid output, micro precision/recall/F1 were
0.952/0.832/0.888. High precision and lower recall are appropriate for this interface: omitting an
uncertain highlight is less misleading than flashing an incorrect phrase. Translation remains
visible whenever alignment is absent or invalid.

The graph correctly separated every repeated source/target occurrence test with an explicit target
counterpart. It also emitted crossing and noncontiguous links in Spanish, German, Russian, Chinese,
and Japanese cases, although it did **not** recover every hand-annotated discontinuous edge. That
distinction matters: the representation supports these structures, but the measured model recall
does not justify calling the challenge set solved.

## Alternatives and relationship to prior work and products

| Approach | Evidence and decision |
| --- | --- |
| Joint translation plus invented chunks | Historical baseline only. One semantic ID could span both repeated phrases or a whole clause, so correct offsets still produced repeated full-clause flashes. It also made a valid translation fail when alignment structure was invalid. |
| Complete translation, then fixed-token Gemini alignment | **Adopted.** This follows the usual word-alignment formulation, gives repeated occurrences stable identities, permits crossing/noncontiguous and many-to-many edges, and makes validation/retry/cache keys stage-specific. |
| SimAlign | [SimAlign](https://aclanthology.org/2020.findings-emnlp.147/) demonstrates unsupervised word alignment from static/contextual multilingual embeddings. It is a useful non-generative benchmark candidate, but its model runtime is disproportionate for the lightweight service. |
| AWESOME-align | [AWESOME-align](https://aclanthology.org/2021.eacl-main.181/) adapts multilingual masked language models using parallel text and self-training objectives. It is stronger motivation for a trained aligner, but adds PyTorch/model weights and operational cost deliberately excluded here. |
| Cross-language span prediction | [Nagata et al.](https://aclanthology.org/2020.emnlp-main.41/) frame alignment as predicting a target span for a source span. This is attractive if professionally annotated in-domain spans become available, but a single contiguous prediction is not by itself enough for the discontinuous links required by the player. |
| LLM token alignment | [Lexi-align](https://github.com/borh-lab/lexi-align) is the closest open implementation reference: it presents indexed tokens to an LLM and validates token links. We adopted fixed IDs, local validation, and graph normalization, but use one schema-constrained call with no hidden repair conversation. |
| Multilingual supervised evaluation | [XL-WA](https://github.com/SapienzaNLP/XL-WA) provides manually annotated English–X word alignments and code around span prediction. It is not vendored: the repository is CC BY-NC-SA and some data requires separate access. The optional `benchmark_io.py` reader accepts externally obtained Pharaoh-style files. |
| Vendor character alignment | [Azure Translator alignment](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/how-to/word-alignment) shows a production API exposing source/target character ranges, including one-to-many and noncontiguous projections. It informs the public shape but is not a dependency. |
| Translate each timing cue | Rejected. It would trade alignment simplicity for lost sentence context and unstable grammar in verb-final, free-word-order, and pro-drop languages. |

Gemini's [structured-output guidance](https://ai.google.dev/gemini-api/docs/structured-output)
motivated request-specific enum schemas, but schema conformance alone cannot enforce token order,
exclusive linked/unaligned accounting, or semantic correctness. Those checks remain local.

## Challenge set

`challenge-set-v1.jsonl` contains 50 project-created pairs: ten each from Spanish, German, Russian,
Mandarin Chinese, and Japanese into English. Two per language form the development split and eight
per language were locked as held-out. SHA-256:
`d431ff8180e896b509bde68aed1bf11a1afa2309ed49d07810399d3ff9169a2c`.

Each record stores the complete texts, stable token IDs, Python-Unicode character spans, sure and
possible links, unaligned IDs, split, phenomenon tags, provenance, and annotation notes. Chinese and
Japanese use explicitly reviewed lexical segmentation; production returns alignment unavailable
when a whitespace-free CJK sentence has only unreliable fallback segmentation. Punctuation,
whitespace, emoji, and other nonlexical gaps are preserved in the displayed strings but excluded
from the graph.

The set includes all reported browser cases plus separable verbs, compounds, verb-final clauses,
free word order, case, aspect, omitted copulas and arguments, classifiers, `把`/`被`, particles,
counters, relative clauses, polarity, names, fillers, repetitions, and multiword/noncontiguous
correspondence. It is a deliberately difficult engineering challenge set, **not professionally
reviewed gold data** and not a population estimate. Dense sure-link annotation is especially
debatable for auxiliaries, particles, idioms, and implicit arguments.

## Protocol and chronology

- Provider/model: Gemini REST API, `gemini-3.1-flash-lite` alias.
- Translation: `literal-translation-v1`, schema 1, temperature 0.2, prompt SHA-256
  `4dfe0149e8c4eea85b85f3326e4826b163c7c8d700c429d7a017fc57a5ca8969`.
- Alignment: `fixed-token-alignment-v1`, schema 1, temperature 0.0, prompt SHA-256
  `0bd5c606ef7886920dff1d439421db68daf7082b38094c6b6cf38644358f017d`.
- Metrics: standard sure/possible-link AER, edge precision/recall/F1, structural validity, linked
  source/target token coverage, latency, reported token use, and SacreBLEU 2.6.0 chrF++
  (`CHRF(word_order=2)`) as a translation regression indicator.
- No repair generation was made. A failed validation consumed one attempt and produced no graph.
- Prompt candidates were selected only on ten development cases. No prompt was revised after
  inspecting held-out results.

Ten retained outputs from the superseded free-chunk experiment supplied qualitative baseline
evidence without new provider calls; their samples/tokenization differ, so no invented AER is
reported. The new run made 200 network attempts: 192 returned model output and eight pilot attempts
received HTTP 429. The final two calls exercised the real indexed service and persistent cache in
Spanish→Russian. The ten pre-existing baseline outputs are contextual evidence, not counted again
against the current 200-attempt ceiling.

| Chronological stage | Network attempts | Model outputs | Purpose |
| --- | ---: | ---: | --- |
| Historical free-chunk baseline | 0 new | 10 retained | Confirm known repetition/coarse-clause failure modes; not numerically comparable. |
| Three-candidate development pilot | 30 | 22 | Ten cases × edge pairs, adjacency rows, and conservative adjacency at 30 RPM; eight late requests were rate-limited. |
| Missing pilot cells, paced retry | 8 | 8 | Complete the balanced candidate matrix at 10 RPM. |
| Locked alignment-only held-out | 40 | 40 | One production adjacency generation for each untouched test pair. |
| Complete two-stage pipeline | 100 | 100 | One translation and one alignment on its generated target for all 50 pairs. |
| Difficult-case stability | 20 | 20 | Two further alignments for ten cases; no prompt change. |
| Indexed service smoke | 2 | 2 | Spanish→Russian translation plus alignment through jobs and the SQLite stage cache; complete with 9 edges. |
| **New total** | **200** | **192** | Eight transport/quota failures are retained as attempts, not structural outputs. |

## Candidate selection

All 30 output-bearing development cells were structurally valid. The table uses the same ten cases
for each candidate; the eight 429 responses are excluded from semantic metrics and shown separately
above.

| Representation | Valid | Mean F1 | Mean AER | Median latency ms | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Explicit edge pairs | 10/10 | 0.881 | 0.119 | 2,986 | Valid but more repetitive output and lower F1. |
| Source-indexed adjacency | 10/10 | **0.899** | **0.102** | 3,244 | Selected on F1 after equal structural validity. |
| Conservative adjacency wording | 10/10 | 0.884 | 0.116 | **2,913** | Dominated on alignment quality; stopped. |

The source-indexed representation requires exactly one row per source ID in source order. Empty
target arrays encode unaligned source tokens; a separate list accounts for every unaligned target.
The validator rejects unknown/duplicate IDs and edges, row reordering/omission, linked/unaligned
overlap, incomplete target accounting, invalid Unicode bounds, and empty graphs.

## Locked held-out results

One Russian question (`ru-04`) linked `T2` and simultaneously declared it unaligned. It was rejected
with no repair call. The service behavior for this case is a complete translation result with
`alignment_status: failed`, a stable public error code, and a retry button; the internal validator
message is stored only in provider-attempt evidence.

| Metric | Conditional on 39 valid graphs | End-to-end over all 40 |
| --- | ---: | ---: |
| Structural validity | — | 39/40 (97.5%) |
| Micro precision | 0.952 | 0.952 |
| Micro recall | 0.832 | 0.817 |
| Micro F1 | 0.888 | 0.879 |
| Macro F1 | 0.883 | 0.861 (invalid = 0) |
| AER | 0.117 | 0.121 |
| Reported tokens | 27,280 | 27,280 |
| Latency | p50 2,747 ms; p95 3,816 ms; range 1,164–5,668 ms | same |

| Source | Valid | Micro P/R/F1, invalid scored empty | Macro F1, invalid = 0 |
| --- | ---: | ---: | ---: |
| Spanish | 8/8 | 0.933 / 0.882 / 0.907 | 0.905 |
| German | 8/8 | 0.984 / 0.924 / 0.953 | 0.949 |
| Russian | 7/8 | 0.941 / 0.716 / 0.814 | 0.765 |
| Chinese | 8/8 | 0.967 / 0.806 / 0.879 | 0.860 |
| Japanese | 8/8 | 0.946 / 0.726 / 0.822 | 0.828 |

Phenomenon slices are small and descriptive. Among slices with at least three cases, held-out macro
F1 was 0.910 for negation (n=12), 0.920 for one-to-many (n=7), 0.888 for reordering (n=8), 0.855
for aspect (n=7), 0.854 for repetition (n=4), 0.805 for many-to-many (n=4), 0.759 for idioms
(n=4), and 0.614 for explicitly tagged discontinuity (n=3, including the invalid Russian case).
The latter is the clearest remaining semantic weakness.

### Representative graph evidence

The originally missed Spanish phrase now has occurrence-specific, fine links:

```text
no(S7) → not(T8)
sepa(S8) → knowing(T9)
decir(S9) → how(T10), to(T11), say(T12)
no(S11) → no(T13)
```

This full example scored F1 0.902. The former partial routine example scored 0.944 and linked all
major clauses, including `voy(S4) → am(T3), going(T4)` and
`así(S15) → like(T17), this(T18)`.

Repeated occurrences no longer depend on surface-string lookup:

```text
Disfrutemos(S2) → Let's(T3), enjoy(T4)
disfrutemos(S3) → let's(T5), enjoy(T6)
```

The full development example scored F1 0.933. The held-out German `Nein, nein` case scored 1.000,
with `S1 → T1` and `S2 → T2`. Russian `говорил ... говорил` likewise produced `S3 → T3` and
`S5 → T5`. These are the repetition cases where distinct target occurrences exist. Japanese
`できる ... できる` is a different, explicitly noted annotation problem: the English reference
expresses the repetition only once, so unique source IDs cannot force target occurrences that do not
exist.

The weakest valid case was Chinese `别急，慢慢来` (F1 0.545). Gemini linked `慢慢(S3)` to
`take(T3), your(T4), time(T5)`, while the annotation assigns more of the idiom to `来(S4)`.
This is a real boundary disagreement rather than a schema failure and illustrates why this challenge
set should not be mistaken for adjudicated gold.

## Complete pipeline and translation review

All 50 translations were structurally valid. Their alignment calls validated in 49/50 cases; the
same class of target-accounting conflict occurred once. Mean source/target linked-token coverage
over valid graphs was 0.908/0.922 (medians 0.950/1.000; minima 0.625/0.600). Translation calls had
p50 latency 1,510 ms and alignment calls 2,295 ms. Across both stages, 44,625 tokens were reported;
overall p95 per-call latency was 3,681 ms.

Mean chrF++ was 78.1. By source language it was Spanish 83.8, German 80.8, Russian 84.9, Chinese
64.8, and Japanese 76.1. chrF++ is sensitive to valid paraphrases and is not a semantic judge. For
example, `¿Por qué no funciona?` became `Why does it not work?` (27.4) rather than the reference
`Why isn't it working?`; the polarity and meaning are correct. Chinese `这件事不是我做的。` became
the more literal `This matter was not done by me.` (17.5) rather than the freer cleft reference; this
is not a severe adequacy failure.

The implementer manually reviewed all 50 generated translations, non-blindly, using six dimensions:
adequacy, polarity, named entities, register, literal comparability, and fluency. A severe result
changes meaning/polarity/entity; borderline retains the main meaning but has a notable omission,
ambiguity, or learner-facing fluency issue. Outcome: 45 acceptable, 5 borderline, 0 severe.

- `ru-01`: `This book I yesterday finally finished reading.` is adequate and unusually comparable,
  but deliberately literal wording is poor English fluency.
- `zh-07`: `asked the students` is a plausible reading of `让`, but weaker/less causative than the
  challenge reference `made the students`; adequacy is borderline without wider context.
- `ja-04`: `I was rained on` preserves the adversative passive but is awkward learner-facing English.
- `ja-06`: `I had him explain it one more time` preserves the main event but leaves the benefactive
  direction (`to me`) implicit.
- `ja-10`: `Well, I can do it, but...` loses some `ね`/`technically` pragmatic nuance and was marked
  borderline for register/literal comparability.

Names (`Juan`, `Ana`, `Anna`, `Masha`), every explicit negation, and repeated content words were
preserved in the reviewed set. This is one reviewer with no independent bilingual adjudication, so
the counts are documented engineering evidence, not human-evaluation confidence intervals.

## Stability

Eighteen of 20 difficult-case repeats validated. Both failures were the same `ru-04` target-accounting
conflict. For each of the other nine cases, the two normalized edge sets were bit-identical. Valid
repeats had micro F1 0.859, macro F1 0.860, p50 latency 1,770 ms, and 13,372 reported tokens. This is
encouraging repeatability evidence for the tested model snapshot, not a guarantee behind a mutable
hosted model alias.

## Production implications

- Translation and alignment are separate cache keys and append-only provider attempts. Retrying a
  failed graph reuses the translation and makes exactly one new alignment call.
- A graph edge can represent one-to-many, many-to-one, crossing, and noncontiguous correspondence.
  Compatibility character groups are derived one source token at a time; there is no coarse-group
  or repeated-text suppression heuristic.
- Playback highlights the union of neighbors of source tokens intersecting the active timing range.
  Hover/focus explores either direction and click/tap pins a relation. One whole-sentence timing unit
  disables automatic target highlighting because it conveys no information.
- Missing links remain unhighlighted. Invalid alignment never hides a valid translation and no raw
  provider/validator message reaches the learner UI.
- Forced-alignment timing is intentionally absent from the semantic cache key. Better timings project
  onto the existing token graph immediately; changed source text/tokens/tokenizer identity do create
  a new key.

The numerical promotion thresholds passed. The graph also demonstrated the required structural
support for noncontiguous links in every language family, but strict recovery of all annotated
noncontiguous edges did not pass. Production therefore exposes the graph while keeping the
high-precision, best-effort UI policy: uncertain/missing edges create no highlight, and a structurally
invalid graph creates no semantic highlighting at all.

## Reproduction, artifacts, and licenses

Build and validate the committed set without a provider call:

```bash
uv run python experiments/target-language-word-alignment/build_challenge.py
uv run pytest tests/test_translation_alignment_experiment.py
```

Run the networked phases (the API key is loaded from exported variables or ignored `.env`):

```bash
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase pilot --rpm 30 \
  --output data/experiments/target-language-word-alignment/pilot.json
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase pilot --variant adjacency \
  --case-id zh-01 --case-id zh-02 --case-id ja-01 --case-id ja-02 --rpm 10 \
  --output data/experiments/target-language-word-alignment/pilot-retry-adjacency.json
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase pilot --variant conservative \
  --case-id es-01 --case-id es-02 --case-id de-01 --case-id de-02 --rpm 10 \
  --output data/experiments/target-language-word-alignment/pilot-retry-conservative.json
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase heldout --rpm 10 \
  --output data/experiments/target-language-word-alignment/heldout.json
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase pipeline --rpm 10 \
  --output data/experiments/target-language-word-alignment/pipeline.json
uv run --extra experiments python \
  experiments/target-language-word-alignment/run_experiment.py --phase stability --rpm 10 \
  --output data/experiments/target-language-word-alignment/stability.json
```

Raw prompt schemas, token lists, provider outputs, normalized graphs, validation errors, per-example
metrics, latency, usage, and generated translations are checkpointed under ignored
`data/experiments/target-language-word-alignment/`. This run's files are `pilot.json` (30 attempts),
`pilot-retry-adjacency.json` and `pilot-retry-conservative.json` (four attempts each), `heldout.json`
(40), `pipeline.json` (100), and `stability.json` (20); the indexed-service smoke is retained in the
separate translation SQLite attempt log. New commands default to one output file per phase so these
records are not overwritten. They are excluded from Git because they contain
the complete generated evaluation corpus, not because short critical excerpts cannot be documented.
No API key is serialized. Most challenge sentences and all link annotations were created for this
project; `es-01` through `es-08` are short user-supplied caption excerpts retained as documented
browser cases. No full transcript is published. External XL-WA content is neither downloaded nor
redistributed.

## Limitations

- The project-created annotations have one author, no independent adjudication, and no confidence
  labels. Function-word and idiom links are especially subjective; possible-link use is sparse.
- The development split has only two cases per language and phenomenon denominators are small.
- chrF++ has one reference per case and penalizes acceptable paraphrases.
- The manual translation review is unblinded and performed by the implementer.
- No SimAlign, AWESOME-align, Lexi-align, or XL-WA runtime was benchmarked; they are researched
  alternatives, not empirical competitors in the tables above.
- The 30-RPM pilot caused eight 429 responses. Their slower retries are reported separately rather
  than silently replacing the original evidence.
- Gemini is a hosted mutable service. Prompt and dataset hashes make reruns comparable but cannot
  freeze provider weights.

## Dated bibliography (accessed 2026-09-07)

- Sabet et al. 2020. [SimAlign: High Quality Word Alignments without Parallel Training Data Using Static and Contextualized Embeddings](https://aclanthology.org/2020.findings-emnlp.147/).
- Dou and Neubig 2021. [Word Alignment by Fine-tuning Embeddings on Parallel Corpora](https://aclanthology.org/2021.eacl-main.181/) (AWESOME-align).
- Nagata et al. 2020. [A Supervised Word Alignment Method based on Cross-Language Span Prediction](https://aclanthology.org/2020.emnlp-main.41/).
- Borh Lab. [Lexi-align](https://github.com/borh-lab/lexi-align).
- SapienzaNLP. [XL-WA](https://github.com/SapienzaNLP/XL-WA).
- Microsoft. [Find sentence and word alignment information](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/how-to/word-alignment).
- Google. [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output).
- Google. [Gemini 3.1 Flash-Lite model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite).
