# Template: language catalogue session

**Kind:** Repeatable content session, not a numbered plan

**Depends on:** Plan 02 for the catalogue schema; Plan 03 for usable search in the new language
(`ja`, `ko` and `zh` additionally require Plan 03's optional analyzer, which the default lemmatizer
does not cover)

Run this template once per source language. It is deliberately not a numbered plan: it is one
reviewable change per language, repeated as often as the owner wants new languages, and the roadmap
index tracks which languages are done.

## Outcome

A curated, executable source catalogue for one language: schema-valid entries, a small enabled
starter set, and a dated coverage report backing the choices.

## Which languages, and why

The default target set is Duolingo's 2025 global top ten minus Spanish, which Plan 02 migrates:
English, French, Japanese, German, Korean, Italian, Chinese, Portuguese, and Hindi. The
[2025 Duolingo Language Report](https://blog.duolingo.com/2025-duolingo-language-report/) orders the
top ten as English, Spanish, French, Japanese, German, Korean, Italian, Chinese, Portuguese, Hindi.
This is a prioritization proxy, not a claim that Duolingo represents every learner population; add or
reorder languages freely.

Default order, though every row is independently completable:

1. English (`en`)
2. French (`fr`)
3. German (`de`)
4. Italian (`it`)
5. Portuguese (`pt`)
6. Japanese (`ja`)
7. Korean (`ko`)
8. Chinese (`zh`)
9. Hindi (`hi`)

Latin-script languages with the smoothest continuity from the Spanish pipeline come first,
deliberately leaving room to improve tokenization and alignment before the CJK and Hindi rollout.

## Decisions that apply to every language

- One file per source language under `config/channels/`, named by its BCP-47 tag.
- Aim for a useful shortlist and roughly four complementary starter channels; use fewer when caption
  availability is poor and more only when they add real coverage.
- Prefer contemporary native speech. Learner-oriented channels are allowed when speakers are native
  and the speech remains useful evidence; they must not dominate the enabled set.
- Cover at least three speech styles and, where applicable, more than one major regional variety.
  Include both clear controlled speech and natural conversational material.
- Creator-authored or original-language automatic captions are acceptable. A channel whose recent
  sample has no usable source-language captions cannot be enabled.
- Do not treat advertised YouTube auto-translations as caption coverage. Prefer stable original
  native-speech sources, but assess formats such as music, Shorts, dubbing, or mirrors on their
  actual retrieval value, rights and provenance, and acquisition reliability rather than banning whole
  categories in advance.
- The existing Spanish catalogue's editorial strength is its descriptions and its deliberate mixture
  of varieties and speech styles. Match that standard.
- Generated channel suggestions cannot be accepted without live checks and human review; availability,
  caption coverage, embedding permission, and upload activity all change over time.

## Session steps

Complete all of the following in one reviewable change:

1. Research a manageable set of candidates from varied editorial categories. Record why each
   shortlisted channel contributes a distinct region, genre, register, speaker profile, or acoustic
   condition.
2. Verify the canonical channel URL, current activity, dominant spoken language, and native-speech
   suitability. Check embedding when selecting the starter set rather than exhaustively policing it.
3. Probe a small recent sample for promising candidates. Record counts for manual source captions,
   automatic source captions, missing captions, acquisition failures, duration, and embedding
   restrictions.
4. Write schema-valid catalogue entries with stable kebab-case IDs, human-readable variety and
   speech-style arrays, and a concrete description of retrieval value and limitations.
5. Enable a small complementary starter set, normally four, containing both clear and natural speech.
6. Run a bounded real acquisition across the enabled channels and confirm round-robin selection does
   not collapse onto one source.
7. Add a dated report under `experiments/channel-catalogues/<language>/` containing the probe method,
   aggregate results, decisions, and rejected candidates. Generated raw probe data remains ignored.
8. Update the language tracker table in `docs/plans/README.md`.

## Acceptance checks

- The language file passes schema validation and explains the chosen starter set.
- Every enabled channel has a dated coverage sample and at least one successfully cached source
  transcript.
- The catalogue records a meaningful mix of styles and varieties and explains exceptions for
  languages with limited regional variation or caption availability.
- A smoke acquisition, index build, language-scoped search, and status query succeeds for the new
  language, using the analyzer that language actually has.
- Human review confirms names, URLs, descriptions, language identity, and native-speech suitability.
- Catalogue entries contain no personal notes, credentials, cookies, or machine-specific paths.

## Non-goals

- Guaranteeing permanent YouTube availability or equal corpus volume across languages.
- Exhaustively listing every valuable channel or publishing downloaded media.
- Implementing morphology, forced alignment, or translation for the new language.
- Enabling all curated candidates by default.
