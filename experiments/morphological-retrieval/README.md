# Morphological retrieval and production token-position benchmark

Re-measured on 2026-09-06 with Python 3.14.7 on macOS-26.5.1-arm64-arm-64bit-Mach-O. This is a local prototype
measurement, not an analyzer accuracy evaluation. [Machine-readable results](results.json) include
analyzer identity, settings, input checksum, and every measured query.

## Method

The ten cached Spanish transcripts were copied into a temporary versioned corpus. Their original
captions and metadata and the existing local index were not changed. Both analyzers indexed the
same 3,264 reconstructed segments and 102,898 physical surface occurrences,
with maximum n-gram length five. Database schema 3 stores surface and lemma tokens once with their
segment positions. It derives occurrences from adjacent positions instead of materializing every
one-to-five-token lookup key.

Initialization measures the first analyzer lookup and one short analysis, including simplemma's
first dictionary load. Build time is then measured with the analyzer initialized. Each of eight
queries has one warm-up followed by 20 measured requests in each of exact and auto
modes; the reported p95 is the observed 95th-percentile sample. Query timings include SQLite reads,
analysis, candidate matching, all occurrence counts, ranking, and result construction (limit 20).
The frequent-word case intentionally exercises the larger candidate set. A separate prefix probe
measures exact and lemma queries at each length from one through five tokens. It records only length,
timing, and occurrence count, not source text. No Stanza model was loaded or downloaded by this
benchmark.

## Results

| Measurement | Unicode only | simplemma 1.2.0 |
| --- | ---: | ---: |
| Initialization, ms | 0.288 | 276.325 |
| Index build, seconds | 2.255 | 2.604 |
| SQLite size, MiB | 14.96 | 19.10 |

| Auto query | Surface occurrences | Expanded occurrences | Unicode median / p95, ms | simplemma median / p95, ms |
| --- | ---: | ---: | ---: | ---: |
| `casa` | 14 | 15 | 1.407 / 1.551 | 2.118 / 2.298 |
| `estar` | 24 | 499 | 1.933 / 2.073 | 28.025 / 33.065 |
| `la verdad` | 42 | 42 | 4.273 / 4.450 | 10.322 / 12.689 |
| `que` | 1215 | 1215 | 72.033 / 83.004 | 82.108 / 94.342 |

| Prefix length | Simplemma exact median / p95, ms | Simplemma lemma median / p95, ms |
| ---: | ---: | ---: |
| 1 | 1.203 / 1.653 | 1.829 / 2.800 |
| 2 | 1.252 / 2.183 | 1.967 / 4.569 |
| 3 | 1.187 / 1.944 | 1.164 / 1.374 |
| 4 | 1.257 / 1.654 | 1.199 / 1.382 |
| 5 | 1.588 / 2.827 | 1.249 / 1.409 |

Against the previously recorded schema 2 result for the identical caption checksum, schema 3 reduced the
Simplemma database from 81.46 MiB to 19.10 MiB (76.6%) and the Unicode database from 58.07 MiB to
14.96 MiB (74.2%). Prefix latency stayed essentially flat through five positions in this sample.
Query cost still grows with the number of returned occurrences: `estar` expands to roughly 500
matches and `que` has 1,215 matches.

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
