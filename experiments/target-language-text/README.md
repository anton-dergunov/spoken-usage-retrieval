# Target-language translation and alignment evaluation

## Decision and scope

This experiment asks two separate questions:

1. Can `gemini-3.1-flash-lite` produce a faithful, literal-leaning but grammatical translation of
   short Spanish speech captions into English and Russian?
2. Can the same single generation produce semantic groups fine and reliable enough to drive
   progressive target-line highlighting?

Those questions must not be conflated. A good translation can have unusably coarse alignment, as
the browser examples below demonstrate. The production decision is therefore two-layered: request
compact two-sided groups in the prompt, then deterministically omit unsafe groups from highlighting.
Translation text remains visible even when one or more alignments are suppressed.

The evaluator makes exactly one provider request per example. It does not repair, retry, or ask a
judge model. A response is valid only when source chunks reconstruct the input exactly at the
Unicode string level, the target is nonempty, and every positive semantic ID occurs on both sides.

## Data, configuration, and criteria

- Source population: the local indexed Spanish corpus, including creator-authored and automatic
  captions. Diverse runs select evenly spaced rows after deterministic ordering; targeted runs use
  the three segment IDs reported from browser testing.
- Targets: English (`en`) and Russian (`ru`).
- Provider/model: Gemini REST API, `gemini-3.1-flash-lite`, temperature `0.2`, structured JSON
  output, output schema version 1.
- Sampling unit: one source segment and one target language. Repeated targeted generations are
  independent provider calls with identical inputs.
- Structural success: exact source reconstruction, nonempty translation, complete target chunk
  coverage, and equal nonzero group-ID sets on source and target.
- Display safety: no positive group spans more than four source lexical tokens or six target
  lexical tokens, no group combines an adjacent repeated token sequence, and no displayed group intersects
  more than four source timing cues. These are conservative UI heuristics, not claims of linguistic
  correctness.
- Translation review: preservation of meaning, polarity, named entities, register, repetitions,
  discourse markers, and uncertainty; literal comparability without ungrammatical target-language
  word order.

Latency p50 values below use the conventional median and p95 uses the nearest-rank observation.
Acceptance percentages always use provider calls as the denominator, not only parseable responses.

## Original 200-call development run

The statement “200 calls” referred to all prompt-development stages combined, not 200 calls on the
first or final prompt. The exact chronology was 4 + 15 + 33 + 33 calls on v1-family variants, then
115 calls on v2: 200 total, split 102 English and 98 Russian.

| Stage | Calls | Accepted | p50 ms | p95 ms | What changed or was learned |
| --- | ---: | ---: | ---: | ---: | --- |
| v1 pilot | 4 | 4 | 2,283 | 4,416 | Established that structured chunks, exact reconstruction, and useful translations were feasible. |
| v1 expansion | 15 | 14 | 1,703 | 2,771 | One response created a positive ID on only one side and was rejected. Later v1 variants temporarily normalized such IDs to unaligned group 0. |
| v1 edge-case pass A | 33 | 32 | 2,331 | 6,124 | One response emitted an empty chunk; zero-length chunks were ignored. Ten accepted results carried warnings: four local one-sided-ID normalizations and six provider warnings. |
| v1 edge-case pass B | 33 | 33 | 2,479 | 6,536 | Confirmed the parser changes; five warnings remained: three one-sided normalizations and two provider warnings. |
| v2 naturalness pass | 115 | 115 | 2,954 | 6,112 | Added an explicit requirement that literal translations remain grammatical and natural. Twenty-one results still needed one-sided-ID normalization and eleven had provider warnings. |

Across these 200 calls, the contemporaneous validators accepted 198 and rejected two. Median
latency was 2.54 seconds, p95 was 6.11 seconds, and Gemini reported 154,827 total tokens. However,
“accepted” during the early stages included locally normalized one-sided IDs; it is therefore not a
strict-schema validity estimate. The 47 total warnings (28 normalization diagnostics and 19
provider warnings) made that weakness visible and led to restoring strict rejection.

### Why the prompt changed

The v1 prompt asked for learner-facing literalness, exact source reconstruction, and shared
semantic IDs. Review found a real tension between literalness and target-language grammar. For
example:

> `A mí me gustan más los huevos hervidos,`<br>
> v1 English: `To me I like more the boiled eggs,`<br>
> Russian: `Мне больше нравятся вареные яйца,`

The English preserves Spanish structure too literally and is not idiomatic English; the Russian is
natural. V2 therefore added: preserve source structure only where the target language permits it,
and never reproduce source-language grammar merely to look literal.

V3 removed the tolerant one-sided-ID normalization and instructed Gemini to compare the positive-ID
sets silently before returning. Its separate strict confirmation made 20 calls: 18 passed, while
both English and Russian outputs for the malformed automatic caption `La imagen te des figuró.`
failed because a positive ID existed only on the source side. The strict run had p50 3.09 seconds
and p95 8.20 seconds. This is the intended failure mode: no invalid alignment or partial translation
is shown and no hidden repair call is made.

Representative accepted v3 translations included:

> `Oh, el sábado me iba a ir de campamento`<br>
> `Oh, on Saturday I was going to go camping`<br>
> `О, в субботу я собирался поехать в поход`

and the longer sentence:

> `Hoy vamos a preguntarle a la gente en la calle si les es difícil decir que no, qué tipo de excusas utilizan o qué otras formas tienen de decir que no.`<br>
> `Today we are going to ask people on the street if it is difficult for them to say no, what type of excuses they use or what other ways they have of saying no.`

These support the translation-quality claim, but not the earlier claim that alignment was generally
usable for progressive playback.

## What the original manual review did—and missed

The original “manual spot review” was opportunistic rather than a blinded or independently scored
evaluation. It inspected accepted English and Russian target strings for obvious meaning loss,
unnatural literalism, discourse markers, repetitions, punctuation, and long-fragment coherence. It
also checked aggregate schema validity, group count, aligned-target fraction, warnings, latency,
and usage.

It did **not** replay timing cues against every group, rate alignment granularity, or retain the raw
source/target chunks and character ranges in the first 220-call artifacts. Those JSON files contain
source text, target text, a group count, and coverage—not enough evidence to reconstruct why an
individual highlight behaved badly. Most review effort was therefore on translation and structural
validity, not correspondence quality. The browser test exposed that methodological gap, and the
user's suspicion on this point was correct.

The evaluator now records the full provider chunks, validated character ranges, source cue timing,
suppression reasons, source/target coverage, maximum group width, and per-group playback span. It
also retains compact ID-set and reconstruction diagnostics for invalid outputs.

## Browser failures and root cause

The stored v3 cache proves that offsets were derived correctly. The player did not search for text,
so repeated substrings were not confusing a `find` operation. The provider itself grouped too much
meaning under one ID, and the player faithfully activated that entire target range whenever any
source cue intersected the source range.

| Example | Stored problematic group | Visible consequence |
| --- | --- | --- |
| `Disfrutemos, disfrutemos` | source `[7,31)` mapped to target `[8,32)`, covering both Spanish repetitions and both `Let's enjoy` repetitions | Either spoken occurrence highlighted both target occurrences. |
| `que vamos a hablar sobre recuerdos usando el pasado` | source `[9,61)` mapped to target `[16,72)`, covering the whole English clause | Each successive source cue reactivated almost the whole target clause, producing conspicuous flashing. |
| `A ver, vamos a pensar, por ejemplo…` | six compact groups; the largest source group contained three words | Highlighting followed the spoken units as expected. |

Applying the new safety analysis retrospectively to the three cached results gives:

| Example | Provider groups | Suppressed | Retained source chars | Retained target chars |
| --- | ---: | ---: | ---: | ---: |
| repeated `Disfrutemos` | 7 | 2 | 53.1% | 48.7% |
| coarse `decía…pasado` clause | 3 | 1 | 11.3% | 19.2% |
| good `A ver…` control | 6 | 0 | 85.7% | 87.5% |

Low retained coverage is acceptable here: an untranslated-looking static target line is less
misleading than a large semantically uncertain region flashing repeatedly. The translation remains
fully readable. Both the Python validator and React player apply the filter; the player-side check
also protects users of old cache rows or externally supplied groups.

## Alignment-focused prompt iterations

The three reported browser segments were generated five times per language for v4 and v5, then the
repeated case six times per language for v6. These calls specifically exercised alignment output,
not merely translation wording.

| Prompt | Calls accepted | Provider/display groups | Suppressed results | p50 / p95 ms | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| v4 | 30/30 | 229 / 224 | 5/30 | 2,216 / 3,180 | Added compact-group and distinct-repeat guidance. All English repetitions and both coarse-clause targets were safe; all five Russian repeated examples still contained one 14-source-word group, which the filter suppressed. |
| v5 | 30/30 | 231 / 223 | 8/30 | 2,056 / 2,555 | Made three-source/five-target-token limits hard. Fourteen-word groups disappeared; only the repeated sentence produced four-source-word auxiliary groups. This showed the original three-token threshold was too strict for honest units such as `vamos a estar tomando`. |
| v6 repeated | 12/12 | 109 / 109 | 0/12 | 2,007 / 2,328 | Calibrated the contract to four source and six target words. Every run assigned distinct IDs to the two repetitions; no displayed group crossed more than four timing cues. |
| v6 diverse | 27/40 | 207 / 207 among valid results | 0/27 | 1,741 / 3,766 | Failed as a general prompt: hard limits encouraged token-by-token source IDs. English passed 18/20 but Russian only 9/20; all 13 failures had source-only IDs. |
| v7 stopped early | 1/5 | 3 / 3 among valid results | 0/1 | 1,153 / 1,153 | Target-first construction and schema ordering further harmed compliance: three ID-set failures and one exact-source reconstruction failure. The run was deliberately stopped rather than spending the planned 40 calls. |
| v8 diverse | 38/40 | 240 / 227 among valid results | 8/38 | 1,437 / 2,432 | Returned to v3 structure with soft granularity guidance. English passed 20/20 and Russian 18/20. The safety filter removed 13 risky groups; no displayed group crossed more than four cues. |
| v8 browser confirmation | 18/18 | 118 / 113 | 5/18 | 1,714 / 21,786 | Three runs per browser case and language. Repetitions always received distinct IDs; five auxiliary-phrase groups in the repeated sentence were conservatively hidden. The other two cases needed no suppression. |

V6 is a useful negative result. It solved the three visible cases but overfit the targeted set and
reduced diverse strict validity from v3's 18/20 to 27/40. V7 showed that changing output order was
not a repair. Production v8 therefore returns to the v3 construction, keeps only soft short-phrase
and distinct-repeat guidance, and delegates hard display safety to deterministic code.

The two v8 failures were Russian translations of `Eh, decía que la angustia` and a noisy automatic
caption containing `al al último sobre primero`; both had source-only semantic IDs and were rejected
after their single call. Among the 38 accepted results, eight contained at least one display-risky
group. Examples included a repeated-token group for `al al` and five-to-eight-word groups in noisy
or long clauses. Suppressing these groups reduced median displayed character coverage to 89.1% on
the source and 88.5% on the target, while retaining all translation text.

The final 18-call browser confirmation passed structurally in every case. For the repeated sentence,
all six English/Russian generations separated the two `Disfrutemos` occurrences. Five runs also
contained a five-source-word auxiliary phrase, which was suppressed; none could reproduce the old
double-highlight. The coarse `decía…pasado` case and the good control each produced only safe groups
in all six generations. Seventeen calls completed in 1.38–2.34 seconds; one Russian control call was
a 21.79-second latency outlier, which is why the small-run p95 equals that maximum.

The complete development record now comprises 396 completed provider generations: the initial 200,
the 20-call strict v3 confirmation, one earlier end-to-end HTTP call, and 175 alignment-focused v4–v8
calls. Ten additional sandboxed v7 attempts failed before reaching Gemini and are excluded from that
provider-call total; they contain no model output and were stopped before the network-enabled run.

## Production policy

- The prompt asks for compact semantic phrases and separately IDs repeated occurrences, but does not
  force one ID per word.
- Strict schema validation still rejects any one-sided positive ID after the one provider call.
- A deterministic quality pass omits groups wider than four source or six target lexical tokens and
  groups that combine an adjacent repeated token sequence (including a repeated multiword phrase).
- Target ranges activate only when a displayed group's source range intersects the currently active
  source timing range. Future forced-alignment timings automatically make that activation finer.
- The valid translation is never discarded merely because a group is suppressed. The UI shows the
  line without uncertain highlighting and reports aggregate diagnostics without exposing text.
- No automatic retry was added. Retrying a semantically valid translation solely for prettier
  highlighting would violate the one-call contract, add nondeterministic latency/cost, and can
  replace good wording with worse wording. Schema-invalid outputs remain terminal and diagnosable.

## Reproduction and artifacts

The networked evaluator is deliberately excluded from CI:

```bash
uv run python scripts/evaluate_translations.py --calls 40 --rpm 15 \
  --output data/experiments/target-language-text/live-evaluation-v8-diverse.json

uv run python scripts/evaluate_translations.py \
  --segment-id seg_3aee8d1ba208cc2d48dd \
  --segment-id seg_b95f1407c293c6b3fa5e \
  --segment-id seg_fbeef46370255b61c444 \
  --repetitions 5 --calls 30 --rpm 15
```

Every new report records model, prompt version and SHA-256, schema version, sampling parameters,
timestamps, raw provider chunks, validated ranges, timing-derived metrics, latency, token usage, and
failures. Reports are checkpointed after every call. The API key comes from the process environment
or ignored `.env` and is never serialized.

Raw reports remain under ignored `data/experiments/target-language-text/` because they contain many
third-party caption excerpts and generated translations. This committed report intentionally uses
only short excerpts needed to document and criticize system behavior. The earlier brevity was partly
over-cautious about reproducing caption text, but the larger problem was that the evaluator had not
retained alignment evidence. This is a practical data-minimization choice, not a legal conclusion;
short research excerpts are appropriate evidence here, while publishing the full sampled corpus
would be unnecessary.

## Limitations

- Manual review was performed by the implementer, was not blinded, and has no independent bilingual
  Russian rater or numeric adequacy/fluency labels. Russian judgments should be treated as spot
  checks plus structural evidence, not a benchmark-quality human evaluation.
- Character coverage and token-width thresholds measure display risk, not semantic correctness.
- The targeted repeats establish repeatability for three known cases but do not estimate population
  frequency. The deterministic diverse sample is small.
- The original v1–v3 artifacts did not record prompt text or hashes. Their chronology and changes
  were reconstructed from the retained reports and implementation history; exact literal old prompt
  strings cannot be recovered. V4 and later reports close this provenance gap.
- Automatic-caption defects can make both translation and alignment intrinsically ambiguous.
- Provider behavior can change behind a stable model name; prompt hashes and raw ignored reports are
  required to compare future reruns.
