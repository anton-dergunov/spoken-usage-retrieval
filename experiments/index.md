# Experiment index

Small, reproducible investigations that inform product and pipeline decisions live here. Generated caption files and acquisition reports remain under the gitignored `data/experiments/` directory. Small derived benchmark metrics may be committed alongside the experiment when they contain no caption text.

| Experiment | Date | Status | Main finding |
| --- | --- | --- | --- |
| [Target-language translation and alignment](target-language-text/README.md) | 2026-09-06 | Complete | Gemini produced faithful English/Russian translations with usable semantic chunks; strict v3 validation accepted 18/20 and safely rejected two inconsistent alignments from one malformed ASR sentence. |
| [Ten-language morphology and compact index](morphological-retrieval-multilingual/README.md) | 2026-09-06 | Complete | Stanza led coverage-adjusted quality in all ten languages; token positions preserved retrieval semantics while reducing measured storage by 85.1%. Plan 04a promotes both findings. |
| [Morphological retrieval](morphological-retrieval/README.md) | 2026-09-05 | Complete | Default lemma retrieval expands `estar` from 24 to 499 occurrences; measured index and query costs are recorded with analyzer limitations. |
| [English track download coverage](english-track-download-coverage/README.md) | 2026-09-04 | Complete | All 10 videos advertised automatic English, but automatic-track retrieval failed with HTTP 429 for 7/7 attempted videos; authored English succeeded for 3/3. |
| [Authored English literalness](authored-english-literalness/README.md) | 2026-09-04 | Complete | Authored translations were generally fluent and semantically useful, but not consistently literal or reliable enough to be the learner-facing authority. |

Each experiment directory contains its qualitative report and the script used to collect or prepare the evidence.
