# Plan 03: Channel catalogue expansion

**Status:** Planned

**Depends on:** Plan 02

## Outcome

Create curated, executable source catalogues for the other nine languages in Duolingo's 2025 global
top ten: English, French, Japanese, German, Korean, Italian, Chinese, Portuguese, and Hindi. Spanish
is migrated in Plan 02. Each language is a separate content session using the same evidence and
acceptance template.

The language set is based on the [2025 Duolingo Language Report](https://blog.duolingo.com/2025-duolingo-language-report/),
which orders the top ten as English, Spanish, French, Japanese, German, Korean, Italian, Chinese,
Portuguese, and Hindi. This is a prioritization proxy, not a claim that Duolingo represents every
learner population.

## Current state

- Only Spanish sources are curated and only four are enabled for acquisition.
- The strong parts of the current catalogue are its descriptions and deliberate mixture of
  varieties and speech styles; new catalogues should preserve that editorial standard.
- Channel availability, caption coverage, embedding permission, and recent upload activity change
  over time, so generated suggestions cannot be accepted without live checks and human review.

## Decisions

- Add one file per source language: `en.json`, `fr.json`, `ja.json`, `de.json`, `ko.json`, `it.json`,
  `zh.json`, `pt.json`, and `hi.json` under `config/channels/`.
- Aim for a useful shortlist and roughly four complementary starter channels per language; use
  fewer when caption availability is poor and more only when they add real coverage.
- Prefer contemporary native speech. Learner-oriented channels are allowed when speakers are native
  and the speech remains useful evidence; they must not dominate the enabled set.
- Cover at least three speech styles and, where applicable, more than one major regional variety.
  Include both clear controlled speech and natural conversational material.
- Creator-authored or original-language automatic captions are acceptable. A channel whose recent
  sample has no usable source-language captions cannot be enabled.
- Do not treat advertised YouTube auto-translations as caption coverage. Prefer stable original
  native-speech sources, but assess formats such as music, Shorts, dubbing, or mirrors on their
  actual retrieval value, rights/provenance, and acquisition reliability rather than banning whole
  categories in advance.

## Implementation work: repeatable language session

For each language, complete all of the following in one reviewable change:

1. Research a manageable set of candidates from varied editorial categories. Record why each shortlisted channel
   contributes a distinct region, genre, register, speaker profile, or acoustic condition.
2. Verify the canonical channel URL, current activity, dominant spoken language, and native-speech
   suitability. Check embedding when selecting the starter set rather than exhaustively policing it.
3. Probe a small recent sample for promising candidates. Record counts for manual
   source captions, automatic source captions, missing captions, acquisition failures, duration,
   and embedding restrictions.
4. Write the useful schema-valid catalogue entries with stable kebab-case IDs, human-readable variety and
   speech-style arrays, and a concrete description of retrieval value and limitations.
5. Enable a small complementary starter set, normally four, containing both clear and natural speech.
6. Run a bounded real acquisition across the enabled channels and confirm round-robin selection
   does not collapse onto one source.
7. Add a dated report under `experiments/channel-catalogues/<language>/` containing the probe method,
   aggregate results, decisions, and rejected candidates. Generated raw probe data remains ignored.

## Session order

Use this default order, but treat every row as independently completable after Plan 02:

1. English (`en`)
2. French (`fr`)
3. German (`de`)
4. Italian (`it`)
5. Portuguese (`pt`)
6. Japanese (`ja`)
7. Korean (`ko`)
8. Chinese (`zh`)
9. Hindi (`hi`)

The order puts languages with simpler continuity from the Spanish/Latin-script pipeline first and
deliberately leaves space to improve tokenization and alignment probes before CJK/Hindi rollout.

## Public interfaces and data

- No new runtime API beyond Plan 02's catalogue schema.
- Experiment reports expose aggregates and channel decisions. Small caption examples may be included
  when redistribution is licensed and they make a finding substantially clearer.
- Catalogue entries are public editorial metadata and must not contain personal notes, credentials,
  cookies, or machine-specific paths.

## Acceptance tests and verification

- Each completed language file passes schema validation and explains the chosen starter set.
- Every enabled channel has a dated coverage sample and at least one successfully cached
  source transcript.
- The catalogue records a meaningful mix of styles/varieties and explains exceptions for languages
  with limited regional variation or caption availability.
- A smoke acquisition, index build, language-scoped search, and status query succeeds for the new
  language once Plan 04 supports its analyzer.
- Human review confirms names, URLs, descriptions, language identity, and native-speech suitability.

## Non-goals

- Guaranteeing permanent YouTube availability or equal corpus volume across languages.
- Exhaustively listing every valuable channel or publishing downloaded media.
- Implementing morphology, forced alignment, translation, or automatic channel recommendation.
- Enabling all curated candidates by default.
