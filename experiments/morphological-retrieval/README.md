# Morphological retrieval benchmark

Measured on 2026-09-05 with Python 3.14.7 on macOS-26.5.1-arm64-arm-64bit-Mach-O. This is a local prototype
measurement, not an analyzer accuracy evaluation. [Machine-readable results](results.json) include
analyzer identity, settings, input checksum, and every measured query.

## Method

The ten cached Spanish transcripts were copied into a temporary versioned corpus. Their original
captions and metadata and the existing local index were not changed. Both analyzers indexed the
same 3,264 reconstructed segments and 102,898 physical surface occurrences,
with maximum n-gram length five. Database schema 2 includes analysis and surface-key metadata in
both baselines; these sizes should not be compared directly with the old schema 1 index.

Initialization measures the first analyzer lookup and one short analysis, including simplemma's
first dictionary load. Build time is then measured with the analyzer initialized. Each of eight
queries has one warm-up followed by 20 measured requests in each of exact and auto
modes; the reported p95 is the observed 95th-percentile sample. Query timings include SQLite reads,
analysis, candidate matching, all occurrence counts, ranking, and result construction (limit 20).
The frequent-word case intentionally exercises the larger candidate set. No Stanza models were
installed or downloaded for this experiment.

## Results

| Measurement | Unicode only | simplemma 1.2.0 |
| --- | ---: | ---: |
| Initialization, ms | 0.188 | 283.953 |
| Index build, seconds | 5.295 | 6.596 |
| SQLite size, MiB | 58.07 | 81.46 |

| Auto query | Surface occurrences | Expanded occurrences | Unicode median / p95, ms | simplemma median / p95, ms |
| --- | ---: | ---: | ---: | ---: |
| `casa` | 14 | 15 | 1.196 / 1.302 | 1.877 / 1.905 |
| `estar` | 24 | 499 | 1.832 / 2.128 | 27.538 / 29.695 |
| `la verdad` | 42 | 42 | 2.678 / 3.111 | 8.414 / 8.534 |
| `que` | 1215 | 1215 | 73.186 / 78.630 | 80.173 / 95.806 |

The additional keys increase disk use and indexing time. Query cost also grows with the number of
retrieved occurrences: `estar` gains many more forms. No timing threshold is asserted in tests;
this measured seed is the baseline for subsequent optimization and Plan 04's accuracy comparison.

## Interpretation and limits

- Simplemma is a dictionary analyzer without context, POS, or morphology features. It cannot expose
  all possible lemmas by itself. Query results report the analyzer output, alternative mappings
  observed in the corpus, and a queried form that is itself an indexed lemma.
- The latter route matters for Portuguese: simplemma 1.2.0 maps `casa` to `casar` but `casas` to
  `casa`. An observed corpus lemma preserves noun retrieval while making the competing analysis
  visible. It does not select a word sense.
- This release also maps English `went` to `wend` and `gone` to `gan`. Consequently a default
  `go` query can miss those forms; the tested irregular family `eat/ate/eaten` works. These are
  documented analyzer limitations, not silently patched exceptions. Stanza is available for
  context-sensitive analysis; selecting the best toolkit remains Plan 04.
- The upstream [simplemma README](https://github.com/adbar/simplemma) describes supported languages,
  limitations, and licensing. Its library-wide size, speed, and accuracy claims are not measurements
  of this application. The installed 1.2.0 wheel is 64.4 MiB; its Python code is MIT licensed and
  its linguistic data has separately documented upstream licenses.
- Stanza's [processor packages](https://stanfordnlp.github.io/stanza/pipeline.html) are restricted to
  tokenization, applicable multi-word expansion, POS, and lemma, using `default_fast` where available.
  [Model downloads](https://stanfordnlp.github.io/stanza/download_models.html) are explicitly disabled
  during indexing/search. Real CJK integration tests skip unless compatible models are present.
- Korean normally uses spaces between eojeol units. Its real integration fixture preserves natural
  spacing; the mocked adapter also tests contiguous Hangul input. This does not claim that Stanza
  can recover arbitrary missing Korean spaces.
- Suggestions filter only curated function words for a language; all search modes keep stopwords
  searchable. Original source text and code-point offsets remain authoritative for highlighting.

## Reproduce

```bash
uv sync --locked --extra dev
uv run python experiments/morphological-retrieval/benchmark.py --data-dir data --repeats 20
```

The benchmark accepts either the versioned Spanish cache or the original ten-video legacy cache.
Legacy conversion exists only inside this experiment. Output contains metrics and a caption checksum,
not caption text. Raw captions are not bundled with the repository. The committed JSON contains
only derived measurements; it is intentionally public evidence rather than a raw acquisition report.
