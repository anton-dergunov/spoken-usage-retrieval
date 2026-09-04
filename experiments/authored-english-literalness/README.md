# Authored English literalness

## Question

Are creator-authored English subtitles sufficiently accurate and literal to serve as the learner-facing translation for a Spanish usage example?

For a general video viewer, fluent subtitles can prioritize natural English, brevity, and contextual interpretation. A dictionary-oriented language-learning viewer has a different requirement: its translation should preserve the meaning and grammatical contribution of the Spanish expression being demonstrated.

## Method

The experiment used the three videos whose authored English tracks downloaded successfully in the [English track download coverage experiment](../english-track-download-coverage/README.md).

- Spanish captions were reconstructed into complete utterances using the application pipeline.
- English cues were associated by timestamps falling inside each Spanish utterance.
- Thirty-three utterances were sampled at regular positions across the three videos.
- Additional targeted examples covered idioms, discourse markers, tense and mood, voice changes, contextual additions, and suspicious cue mappings.
- Cue timing coverage was checked independently from translation quality.

Prepare or refresh the authored tracks, then generate a reproducible comparison sample:

```bash
uv run python experiments/english-track-download-coverage/probe_english_captions.py
uv run python experiments/authored-english-literalness/compare_authored_captions.py
```

The comparison JSON is written to `data/experiments/authored-english-literalness/comparison.json`.

## Timing observations

The authored tracks aligned very closely at the cue level:

| Video | Spanish cues | English cues | Equal start timestamps |
| --- | ---: | ---: | ---: |
| `c3eyUEFUDMk` | 125 | 126 | 124 |
| `hjgiXM9zUiY` | 289 | 289 | 289 |
| `jB04l33EUVE` | 216 | 216 | 215 |

This makes the tracks technically useful as context. It does not establish literalness: translation boundaries and grammatical choices can still differ while timestamps match.

## Representative results

### Close and useful translations

| Spanish | Authored English | Assessment |
| --- | --- | --- |
| `En la lavandería hay lavadoras y secadoras.` | “In the laundromat there are washing machines and dryers.” | Close and complete. |
| `Estoy sacudiendo esta falda.` | “I'm shaking out this skirt.” | Accurate, with a natural phrasal verb. |
| `Me lo conozco de memoria.` | “I know it by heart.” | Strong idiomatic equivalence. |
| `Voy a ver si la manguera que tengo me vale para el grifo que hay.` | “I'm going to see if the hose I have fits the tap here.” | Faithful while resolving natural English syntax. |

### Useful meaning, but not literal evidence

| Spanish | Authored English | Concern for learning use |
| --- | --- | --- |
| `Y nada.` | “So yeah.” | Natural discourse equivalent, but it hides the Spanish construction. |
| `¡Qué fuerte!` | “Wow!” | Communicative effect survives, while the lexical and idiomatic content disappears. |
| `Me acaba de preguntar…` | “I was just asked…” | Changes active Spanish into passive English. |
| `Tienes un ojo de pez.` | “You have a fisheye lens.” | Adds a contextual noun absent from the source surface form. |
| `Y simplemente es que…` | “Maybe it's just that…” | Adds an explicit uncertainty reading. |

### Material risks

| Spanish | Authored English | Problem |
| --- | --- | --- |
| `Iré esta tarde al supermercado.` | “*lie I'll go to the supermarket this afternoon.” | Adds editorial commentary not spoken in the Spanish cue. |
| `Me acaba de preguntar el policía que hay ahí detrás en el coche.` | “I was just asked why the policeman back there in the car…” | Contains an apparent `by`/`why` error and distributes the construction poorly across cues. |
| `Si no hubiera tenido que usar la garrafa…` | “Otherwise I would have had to use the jerrycan…” | Resolves ambiguous or faulty Spanish punctuation by imposing a different visible grammatical structure. It may reflect the intended speech, but is unsafe as direct evidence about the captioned construction. |

The controlled beginner lesson was substantially more literal than the two conversational vlogs. Authored-subtitle suitability therefore varies by channel and content style and cannot be inferred from track provenance alone.

## Conclusion

Creator-authored English is valuable contextual evidence, but it should not be the canonical learner-facing translation. Matching cue timestamps make it convenient to supply to a translator, not trustworthy enough to bypass translation and verification.

The recommended on-demand enrichment call should receive:

- the selected Spanish utterance and target expression;
- nearby Spanish context where needed;
- the authored English as explicitly untrusted reference material;
- a request for a pedagogically faithful English translation;
- semantic alignment groups between source and target character ranges;
- warnings for ambiguity, caption errors, additions, or omissions.

The main displayed translation should be structurally informative without becoming misleadingly word-for-word. For an idiom such as `¡Qué fuerte!`, “How strong!” is formally closer but pedagogically wrong; “That's intense!” or “That's wild!” preserves more of the idiom than the authored “Wow!”. A separate natural alternative can be retained when it materially helps.
