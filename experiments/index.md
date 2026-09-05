# Experiment index

Small, reproducible investigations that inform product and pipeline decisions live here. Generated caption files and acquisition reports remain under the gitignored `data/experiments/` directory. Small derived benchmark metrics may be committed alongside the experiment when they contain no caption text.

| Experiment | Date | Status | Main finding |
| --- | --- | --- | --- |
| [Morphological retrieval](morphological-retrieval/README.md) | 2026-09-05 | Complete | Default lemma retrieval expands `estar` from 24 to 499 occurrences; measured index and query costs are recorded with analyzer limitations. |
| [English track download coverage](english-track-download-coverage/README.md) | 2026-09-04 | Complete | All 10 videos advertised automatic English, but automatic-track retrieval failed with HTTP 429 for 7/7 attempted videos; authored English succeeded for 3/3. |
| [Authored English literalness](authored-english-literalness/README.md) | 2026-09-04 | Complete | Authored translations were generally fluent and semantically useful, but not consistently literal or reliable enough to be the learner-facing authority. |

Each experiment directory contains its qualitative report and the script used to collect or prepare the evidence.
