# Ten-language morphology and compact-index experiment

**Status:** Complete

This experiment compares the production Unicode, simplemma 1.2.0, and Stanza 1.14.0 adapters on Universal Dependencies 2.18. It records raw analyzer behavior; it does not add word-specific lemma overrides or change production selection or storage.

## Coverage

| Language | Unicode | simplemma | Stanza |
| --- | --- | --- | --- |
| en | complete | complete | complete |
| es | complete | complete | complete |
| fr | complete | complete | complete |
| ja | complete | N/A | complete |
| de | complete | complete | complete |
| ko | complete | N/A | complete |
| it | complete | complete | complete |
| zh | complete | N/A | complete |
| pt | complete | complete | complete |
| hi | complete | complete | complete |

Japanese and Chinese need model-based segmentation, and Japanese, Korean, and Chinese need Stanza for morphology. Unicode remains the dependency-free exact-search baseline. simplemma is evaluated only for its seven configured languages; unsupported cells are explicit `N/A` rows.

## Quality

| Language | Analyzer | Boundary F1 | Lemma coverage | Strict lemma | Folded key | Unseen | Ambiguous | MWT |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| en | unicode | 0.960359 | 0.0 | None | None | None | None | 0.0 |
| en | simplemma | 0.960359 | 0.962531 | 0.948493 | 0.948493 | 0.889286 | 0.95936 | 0.0 |
| en | stanza | 0.993362 | 0.992689 | 0.98214 | 0.98214 | 0.901949 | 0.989216 | 0.920904 |
| es | unicode | 0.990203 | 0.0 | None | None | None | None | 0.0 |
| es | simplemma | 0.990203 | 0.968853 | 0.819123 | 0.923954 | 0.787866 | 0.852999 | 0.0 |
| es | stanza | 0.999805 | 0.99981 | 0.880214 | 0.991916 | 0.837716 | 0.922882 | 0.996569 |
| fr | unicode | 0.898375 | 0.0 | None | None | None | None | 0.0 |
| fr | simplemma | 0.898375 | 0.852138 | 0.740657 | 0.890283 | 0.681614 | 0.754366 | 0.0 |
| fr | stanza | 0.99906 | 0.998749 | 0.823141 | 0.984626 | 0.724409 | 0.856119 | 0.985663 |
| ja | unicode | 0.022157 | 0.0 | None | None | None | None | None |
| ja | stanza | 0.677608 | 0.774303 | 0.803841 | 0.963564 | 0.736383 | 0.706038 | None |
| de | unicode | 0.995453 | 0.0 | None | None | None | None | 0.0 |
| de | simplemma | 0.995453 | 0.976361 | 0.822327 | 0.895542 | 0.760529 | 0.868225 | 0.0 |
| de | stanza | 0.997798 | 0.997381 | 0.90413 | 0.982047 | 0.800718 | 0.944858 | 0.992701 |
| ko | unicode | 0.983907 | 0.0 | None | None | None | None | None |
| ko | stanza | 0.97518 | 0.976045 | 0.7475 | 0.811029 | 0.69049 | 0.752784 | None |
| it | unicode | 0.951779 | 0.0 | None | None | None | None | 0.0 |
| it | simplemma | 0.951779 | 0.863267 | 0.829571 | 0.847003 | 0.863281 | 0.798871 | 0.0 |
| it | stanza | 0.998353 | 0.998051 | 0.966482 | 0.98297 | 0.918868 | 0.972647 | 0.994565 |
| zh | unicode | 0.032338 | 0.0 | None | None | None | None | None |
| zh | stanza | 0.738968 | 0.776529 | 0.998128 | 0.998128 | 0.998822 | 0.999008 | None |
| pt | unicode | 0.977771 | 0.0 | None | None | None | None | 0.0 |
| pt | simplemma | 0.977771 | 0.902168 | 0.681576 | 0.799694 | 0.70066 | 0.670947 | 0.0 |
| pt | stanza | 0.998472 | 0.998454 | 0.868045 | 0.984269 | 0.782262 | 0.91519 | 0.996526 |
| hi | unicode | 0.074106 | 0.0 | None | None | None | None | None |
| hi | simplemma | 0.074106 | 0.105362 | 0.930707 | 0.930707 | 0.975904 | 0.918004 | None |
| hi | stanza | 1.0 | 1.0 | 0.552499 | 0.969918 | 0.372312 | 0.570469 | None |

Strict lemma scoring uses Unicode NFC plus case folding and keeps accents. Folded-key accuracy separately reports equivalence under the production search normalizer. Error examples contain only token form, lemma, and POS fields; source sentences are not stored.

## Runtime

| Language | Analyzer | Init s | Tokens/s | Analysis RSS MB | Worker RSS MB | Analyzer MB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| en | unicode | 0.000145 | 322681.164373 | 275.5 | 406.5 | 0.0 |
| en | simplemma | 0.019808 | 160331.686602 | 302.4 | 576.7 | 0.4 |
| en | stanza | 3.100352 | 986.845716 | 751.6 | 1023.4 | 127.8 |
| es | unicode | 0.000161 | 301606.55012 | 545.7 | 896.1 | 0.0 |
| es | simplemma | 0.025147 | 65776.882731 | 678.5 | 1191.7 | 1.7 |
| es | stanza | 3.687262 | 1413.958807 | 1049.0 | 1561.9 | 128.0 |
| fr | unicode | 0.000158 | 338324.529187 | 419.6 | 476.1 | 0.0 |
| fr | simplemma | 0.02085 | 65519.209916 | 460.1 | 569.2 | 0.7 |
| fr | stanza | 3.527475 | 1344.3064 | 880.1 | 880.1 | 124.5 |
| ja | unicode | 0.000197 | 1168310.682401 | 260.7 | 270.2 | 0.0 |
| ja | stanza | 3.024204 | 1794.365635 | 752.0 | 910.2 | 126.6 |
| de | unicode | 0.000152 | 292167.878616 | 343.0 | 438.5 | 0.0 |
| de | simplemma | 0.020642 | 44178.489645 | 497.2 | 673.9 | 2.2 |
| de | stanza | 3.233506 | 996.654479 | 912.0 | 959.0 | 131.7 |
| ko | unicode | 0.00013 | 252150.605828 | 133.2 | 211.0 | 0.0 |
| ko | stanza | 3.126336 | 804.524835 | 611.6 | 751.3 | 128.9 |
| it | unicode | 0.000146 | 342495.940801 | 304.1 | 365.7 | 0.0 |
| it | simplemma | 0.02019 | 62655.144711 | 379.4 | 489.2 | 0.9 |
| it | stanza | 3.166821 | 1219.087294 | 792.3 | 837.2 | 125.3 |
| zh | unicode | 0.000118 | 1425103.512764 | 160.9 | 173.2 | 0.0 |
| zh | stanza | 3.076174 | 1517.146703 | 816.0 | 897.0 | 319.4 |
| pt | unicode | 0.00016 | 334784.15768 | 253.9 | 408.6 | 0.0 |
| pt | simplemma | 0.025721 | 59303.000646 | 410.6 | 693.0 | 2.3 |
| pt | stanza | 2.990537 | 1185.636285 | 727.2 | 893.0 | 122.9 |
| hi | unicode | 0.000146 | 198976.289177 | 563.4 | 754.2 | 0.0 |
| hi | simplemma | 0.020577 | 135052.438425 | 580.4 | 1122.2 | 0.2 |
| hi | stanza | 3.091948 | 1473.761643 | 958.1 | 1455.3 | 113.8 |

## Storage

| Language | Analyzer | Layout | MB | Bytes/gold token | Median ms | p95 ms | Parity |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| en | unicode | dual | 20.98 | 1005.237194 | 0.10877 | 1.186333 | True |
| en | unicode | partial | 16.35 | 783.26525 | 0.113042 | 1.248542 | True |
| en | unicode | token | 4.27 | 204.75321 | 0.131313 | 5.141958 | True |
| en | simplemma | dual | 34.96 | 1674.89623 | 0.117313 | 1.288333 | True |
| en | simplemma | partial | 25.74 | 1233.198264 | 0.121375 | 1.281083 | True |
| en | simplemma | token | 8.75 | 419.238748 | 0.166958 | 5.474667 | True |
| en | stanza | dual | 35.38 | 1695.109527 | 0.119312 | 1.278416 | True |
| en | stanza | partial | 26.08 | 1249.481197 | 0.121292 | 1.289917 | True |
| en | stanza | token | 9.52 | 456.109299 | 0.170834 | 6.088875 | True |
| es | unicode | dual | 46.15 | 1023.913331 | 0.110396 | 0.1575 | True |
| es | unicode | partial | 35.02 | 776.90529 | 0.115937 | 0.165333 | True |
| es | unicode | token | 6.63 | 147.164791 | 0.140834 | 0.268292 | True |
| es | simplemma | dual | 79.79 | 1770.39763 | 0.123 | 0.222875 | True |
| es | simplemma | partial | 57.59 | 1277.85493 | 0.12275 | 0.189333 | True |
| es | simplemma | token | 13.74 | 304.903259 | 0.170145 | 0.404542 | True |
| es | stanza | dual | 81.16 | 1800.731951 | 0.121417 | 0.204375 | True |
| es | stanza | partial | 58.57 | 1299.608972 | 0.132209 | 0.273625 | True |
| es | stanza | token | 15.28 | 339.051037 | 0.175812 | 0.38675 | True |
| fr | unicode | dual | 7.51 | 895.417652 | 0.10702 | 0.220083 | True |
| fr | unicode | partial | 5.61 | 668.535032 | 0.114271 | 0.254334 | True |
| fr | unicode | token | 0.92 | 109.481347 | 0.137937 | 0.648167 | True |
| fr | simplemma | dual | 13.33 | 1589.575978 | 0.110062 | 0.462292 | True |
| fr | simplemma | partial | 9.54 | 1137.674249 | 0.114979 | 0.456541 | True |
| fr | simplemma | token | 1.97 | 235.268426 | 0.149271 | 1.908042 | True |
| fr | stanza | dual | 15.37 | 1832.764331 | 0.113583 | 0.580791 | True |
| fr | stanza | partial | 10.98 | 1309.583258 | 0.118938 | 0.591333 | True |
| fr | stanza | token | 3.0 | 358.260237 | 0.16425 | 2.454458 | True |
| ja | unicode | dual | 1.09 | 124.962712 | 0.098666 | 0.371875 | True |
| ja | unicode | partial | 0.77 | 88.683215 | 0.102 | 0.128917 | True |
| ja | unicode | token | 0.29 | 33.592127 | 0.088917 | 0.119667 | True |
| ja | stanza | dual | 20.11 | 2305.763587 | 0.1165 | 1.290708 | True |
| ja | stanza | partial | 14.25 | 1634.368945 | 0.124396 | 1.377208 | True |
| ja | stanza | token | 2.96 | 339.952324 | 0.205375 | 6.322 | True |
| de | unicode | dual | 12.23 | 907.387642 | 0.105021 | 0.126083 | True |
| de | unicode | partial | 8.99 | 667.060372 | 0.115709 | 0.163334 | True |
| de | unicode | token | 1.32 | 97.696369 | 0.145333 | 0.480625 | True |
| de | simplemma | dual | 22.11 | 1641.125062 | 0.11102 | 0.172417 | True |
| de | simplemma | partial | 15.68 | 1163.369524 | 0.118 | 0.22225 | True |
| de | simplemma | token | 2.89 | 214.236252 | 0.15025 | 0.410125 | True |
| de | stanza | dual | 22.4 | 1662.577677 | 0.111104 | 0.204416 | True |
| de | stanza | partial | 15.88 | 1178.734235 | 0.116896 | 0.210292 | True |
| de | stanza | token | 3.08 | 228.731262 | 0.14875 | 0.404 | True |
| ko | unicode | dual | 13.63 | 1409.211356 | 0.109708 | 0.1505 | True |
| ko | unicode | partial | 9.67 | 999.369085 | 0.114625 | 0.155292 | True |
| ko | unicode | token | 1.3 | 134.864353 | 0.138937 | 0.3165 | True |
| ko | stanza | dual | 27.83 | 2876.971609 | 0.111104 | 0.204417 | True |
| ko | stanza | partial | 19.44 | 2009.236593 | 0.114708 | 0.174084 | True |
| ko | stanza | token | 4.18 | 432.454259 | 0.141937 | 0.424125 | True |
| it | unicode | dual | 7.38 | 837.646855 | 0.104583 | 0.146959 | True |
| it | unicode | partial | 5.45 | 618.590451 | 0.111292 | 0.185291 | True |
| it | unicode | token | 0.8 | 91.347407 | 0.1265 | 0.277333 | True |
| it | simplemma | dual | 13.39 | 1520.091805 | 0.11025 | 0.235084 | True |
| it | simplemma | partial | 9.52 | 1080.205261 | 0.115396 | 0.210709 | True |
| it | simplemma | token | 1.78 | 201.762477 | 0.143833 | 0.752583 | True |
| it | stanza | dual | 14.85 | 1685.492692 | 0.105604 | 0.219625 | True |
| it | stanza | partial | 10.54 | 1195.941539 | 0.113 | 0.266208 | True |
| it | stanza | token | 2.47 | 280.693732 | 0.158417 | 2.63475 | True |
| zh | unicode | dual | 1.4 | 142.50063 | 0.095 | 0.106667 | True |
| zh | unicode | partial | 1.0 | 102.012986 | 0.099916 | 0.112375 | True |
| zh | unicode | token | 0.29 | 29.770327 | 0.088667 | 0.105375 | True |
| zh | stanza | dual | 19.25 | 1955.711988 | 0.110604 | 0.39625 | True |
| zh | stanza | partial | 13.68 | 1390.47272 | 0.115438 | 0.3965 | True |
| zh | stanza | token | 2.84 | 288.970637 | 0.138959 | 1.4705 | True |
| pt | unicode | dual | 19.51 | 854.652241 | 0.104375 | 0.156916 | True |
| pt | unicode | partial | 14.34 | 628.284891 | 0.111604 | 0.166042 | True |
| pt | unicode | token | 1.91 | 83.839759 | 0.132104 | 0.392958 | True |
| pt | simplemma | dual | 35.21 | 1542.138268 | 0.110584 | 0.225875 | True |
| pt | simplemma | partial | 24.91 | 1091.285684 | 0.1205 | 0.369708 | True |
| pt | simplemma | token | 4.12 | 180.683237 | 0.141458 | 0.33325 | True |
| pt | stanza | dual | 37.11 | 1625.293621 | 0.114584 | 0.261 | True |
| pt | stanza | partial | 26.23 | 1148.775805 | 0.124125 | 0.5595 | True |
| pt | stanza | token | 5.1 | 223.458624 | 0.160541 | 0.583375 | True |
| hi | unicode | dual | 54.66 | 1736.301969 | 0.168563 | 1.139792 | True |
| hi | unicode | partial | 40.46 | 1285.258043 | 0.164584 | 1.137916 | True |
| hi | unicode | token | 5.41 | 171.979885 | 5.84375 | 56.173292 | True |
| hi | simplemma | dual | 97.93 | 3110.900212 | 0.16598 | 1.237208 | True |
| hi | simplemma | partial | 69.33 | 2202.235929 | 0.167375 | 1.129792 | True |
| hi | simplemma | token | 10.91 | 346.441442 | 6.16 | 57.908958 | True |
| hi | stanza | dual | 118.25 | 3756.383157 | 0.156667 | 1.643875 | True |
| hi | stanza | partial | 84.64 | 2688.643684 | 0.210417 | 2.020041 | True |
| hi | stanza | token | 14.32 | 454.890518 | 0.784438 | 35.326375 | True |

Physical occurrences, the form lexicon, token rows, and secondary indexes are separated by SQLite `dbstat` in each result. Because exact and lemma keys share pages in the production-shaped key table, `logical_breakdown` separately records their row counts and payload bytes. Compact timing is reported only after occurrence IDs, match routes, counts, and character spans match the dual-key reference.

## Retrieval and anomalous mappings

The query manifest is selected by stable SHA-256 order with seed `20260905`. It includes up to 20 lemmas with at least two observed test forms and five test occurrences, plus up to 10 forms that have multiple train/dev analyses. Results include exact count, deduplicated auto expansion, candidate-lemma recall, intended-lemma precision, and ambiguous-union precision.

| Language | Analyzer | Queries | Exact | Auto | Expansion | Mean lemma recall | Mean lemma precision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| en | unicode | 31 | 1226 | 1226 | 0 | 0.0 | None |
| en | simplemma | 31 | 1226 | 1668 | 442 | 0.781703 | 0.815711 |
| en | stanza | 31 | 1226 | 1729 | 503 | 0.807721 | 0.873845 |
| es | unicode | 33 | 172 | 172 | 0 | 0.0 | None |
| es | simplemma | 33 | 172 | 471 | 299 | 0.903974 | 0.79982 |
| es | stanza | 33 | 172 | 497 | 325 | 0.993987 | 0.863534 |
| fr | unicode | 31 | 471 | 471 | 0 | 0.0 | None |
| fr | simplemma | 31 | 471 | 923 | 452 | 0.780741 | 0.894945 |
| fr | stanza | 31 | 497 | 1183 | 686 | 0.952189 | 0.903862 |
| ja | unicode | 31 | 2 | 2 | 0 | 0.0 | None |
| ja | stanza | 31 | 936 | 3706 | 2770 | 0.843963 | 0.743084 |
| de | unicode | 30 | 89 | 89 | 0 | 0.0 | None |
| de | simplemma | 30 | 89 | 283 | 194 | 0.936 | 0.954747 |
| de | stanza | 30 | 89 | 344 | 255 | 0.97 | 0.966955 |
| ko | unicode | 18 | 84 | 84 | 0 | 0.0 | None |
| ko | stanza | 18 | 84 | 250 | 166 | 0.966922 | 0.790729 |
| it | unicode | 31 | 165 | 165 | 0 | 0.0 | None |
| it | simplemma | 31 | 165 | 1033 | 868 | 0.780293 | 0.864791 |
| it | stanza | 31 | 166 | 1889 | 1723 | 0.451498 | 0.797735 |
| zh | unicode | 21 | 0 | 0 | 0 | 0.0 | None |
| zh | stanza | 21 | 43 | 964 | 921 | 0.711744 | 0.861156 |
| pt | unicode | 31 | 308 | 308 | 0 | 0.0 | None |
| pt | simplemma | 31 | 308 | 2614 | 2306 | 0.807674 | 0.868122 |
| pt | stanza | 31 | 308 | 4141 | 3833 | 1.0 | 0.929723 |
| hi | unicode | 30 | 2218 | 2218 | 0 | 0.0 | None |
| hi | simplemma | 30 | 2218 | 2218 | 0 | 0.241808 | 0.165543 |
| hi | stanza | 30 | 2241 | 4451 | 2210 | 0.996825 | 0.944405 |

simplemma's raw English dictionary maps `went` to `wend`. That mapping is historically explainable but incorrect for ordinary modern-English retrieval, where `went` is the past tense of `go`. The experiment records it as a false positive. The production system intentionally preserves available observed candidates and ranks exact matches first; later ranking and the dictionary-article LLM can reject semantically unsuitable clips.

## Reproduction

```console
uv sync --extra dev --extra nlp
uv run python experiments/morphological-retrieval-multilingual/run.py preflight
uv run python experiments/morphological-retrieval-multilingual/run.py prepare
uv run python experiments/morphological-retrieval-multilingual/run.py run
```

UD files and Stanza weights remain under `data/experiments/morphological-retrieval-multilingual/`. Preparation is the only command that accesses the network. Preflight reports every missing split, package, license mismatch, and model; the run never silently narrows the matrix.

## Verification

The completed run passed all 54 partial/token compact-layout parity checks. Repository verification passed Ruff lint and format checks, mypy, 64 Python tests, the synthetic smoke command, 17 web tests, the production web build, and source/wheel builds. With the experiment's local models selected, all 11 Stanza contract and real Japanese/Korean/Chinese offset and phrase tests passed rather than taking their normal optional-model skips.

## Decision

| Language | Recommended morphology analyzer | Effective lemma score |
| --- | --- | ---: |
| en | stanza | 0.97496 |
| es | stanza | 0.880047 |
| fr | stanza | 0.822111 |
| ja | stanza | 0.622416 |
| de | stanza | 0.901762 |
| ko | stanza | 0.729594 |
| it | stanza | 0.964598 |
| zh | stanza | 0.775075 |
| pt | stanza | 0.866703 |
| hi | stanza | 0.552499 |

The analyzer recommendation maximizes strict lemma accuracy multiplied by end-to-end lemma coverage. Runtime and downstream LLM filtering remain deployment considerations; this experiment does not change production defaults.

The token-position prototype preserves semantics and saves 85.1% across completed rows at 1.34× median query time. This is material enough for a separate production migration plan.

Wiktionary-derived candidates remain deferred. Any analyzer or schema change will use a separate implementation plan.

## Limitations

Two English EWT test inputs consisting of unusually long URL-like tokens caused Stanza to return a document-wide `<UNK>` span. The production adapter correctly rejected those offsets; the scorer records both as `unsupported_input` and counts their words as uncovered instead of weakening the source-span contract. Context-free diagnostic analysis also returns `gone → gone` in Stanza, while the corpus-observed form lexicon can still contribute `go` as an additional query candidate.

UD written-language treebanks are comparable gold data rather than a direct sample of YouTube speech. A UD lemma match can still be unhelpful for a learner, and a linguistically valid ambiguity can lower intended-sense precision. Timings describe the recorded machine and cold-process protocol; they are not service capacity estimates.
