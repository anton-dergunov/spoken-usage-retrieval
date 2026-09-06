# Target-language text live evaluation

This experiment exercises the exact production Gemini prompt and structured-output validator over
representative source segments, split across English and Russian targets. It checks schema validity,
exact source reconstruction, semantic-group consistency, target coverage, latency, and usage while
retaining outputs for manual review of literalness, fluency, and alignment.

It is manual, networked, quota-consuming work and is not part of CI:

```bash
uv run python scripts/evaluate_translations.py --calls 200
```

The resumable report is written after every request to the ignored path
`data/experiments/target-language-text/live-evaluation.json`. The API key is read from `.env` or the
process environment and is never written to the report.

## 2026-09-06 evaluation

Prompt development used 200 paced calls over diverse indexed Spanish caption segments, split 102
to English and 98 to Russian. The validator in effect for each iteration accepted 198 results and
rejected two (one mismatched semantic ID set and one empty-chunk edge case). Median provider latency
was 2.54 seconds, p95 was 6.11 seconds, and Gemini reported 154,827 total tokens. These exploratory
reports remain under ignored `data/` because they contain source and translated captions.

The final `literal-chunks-v3` prompt and strict production validator then received a separate 20-call
confirmation set, evenly split between English and Russian. Eighteen passed with exact source
reconstruction, complete target text, and two-sided semantic groups; two failed after their single
generation call because both translations of the same malformed automatic-caption sentence used a
one-sided semantic ID. Median latency was 3.22 seconds, p95 was 8.20 seconds, and accepted outputs
contained no provider warnings. A final request through the HTTP service completed with eight
alignment groups, and the identical second request was an immediate persistent-cache hit without a
provider operation.

Manual spot review found the accepted English and Russian output faithful, appropriately literal,
and generally fluent, including discourse markers, repetitions, reordered phrases, punctuation,
and longer conversational fragments. An earlier prompt over-preserved Spanish syntax in English;
v2 added the grammatical-naturalness constraint. The strict v3 result shows the remaining weakness
clearly: malformed ASR can make Gemini's semantic chunk IDs inconsistent. Such output is stored as
a diagnosable terminal failure rather than displayed or silently repaired.
