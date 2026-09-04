# Plan 16: Acervo integration and release

**Status:** Planned

**Depends on:** Plans 10–15

## Outcome

Validate the Python wheel, React package, HTTP contract, documented research evidence, and a clean
clone, then provide an executable companion checklist for integrating the service into Acervo. The
repositories remain independently useful and own their natural responsibilities.

## Current state

- This repository can run its API/demo from source but is not yet consumed as packaged artifacts.
- Acervo is a React/PocketBase application with Compose-managed services and a one-shot worker. Its
  example model already stores `videoRef`, `videoTitle`, and `videoStart`, while video opening is not
  wired into the article experience.
- No automated consumer smoke test or checked-in OpenAPI compatibility check spans the package
  boundary.

## Decisions

- Distribute a normal Python wheel and locally packable npm package first. Registry publication is a
  release choice, not required to prove integration.
- Run retrieval as its own foreground Compose service in Acervo with a persistent cache/index volume.
  Do not hide a long-running indexer inside Acervo's deliberately one-shot worker command.
- Before article generation, Acervo queries a bounded candidate set for the requested source
  language and vocabulary item. The article LLM may select only IDs from that set, and Acervo
  validates every returned ID before persistence.
- Extend an example with `videoEnd`, `videoChannel`, and `speechSegmentId`, while retaining
  `videoRef`, `videoTitle`, and `videoStart` as direct-playback/display fallback.
- Store source-caption and matched-form provenance with the selected example where it helps audit
  generation. Fetch richer timing/translation from the service when opening the player.
- Acervo owns the modal and article navigation; `SpeechClipPlayer` owns clip playback and enrichment
  presentation.

## Implementation work

### Retrieval repository

1. Build wheel/sdist and npm tarball, inspect their contents, and install both into clean temporary
   consumers using only documented dependencies.
2. Version and validate the OpenAPI contract against the packaged TypeScript client; add a minimal
   compatibility fixture that exercises search, clip lookup, and translation state.
3. Write concise architecture, corpus/data licensing, operations, benchmark, model-card, and
   limitations documentation. Link experiment reports rather than turning the README into a lab log.
4. Run the public release checklist: clean clone, synthetic smoke corpus, tests/builds, package
   inspection, no credentials, intentional artifact/license review, and tagged-version dry run.

### Companion Acervo checklist

5. Add a supervised Compose service using the wheel/image, an explicit internal base URL, health
   check, model/cache settings, and persistent volume. Keep restart policy and deployment secrets in
   Acervo.
6. Add a narrow retrieval client used before article generation. Request ranked candidates by
   source language and headword/phrase, give the LLM stable IDs plus compact context, and validate
   selected IDs against the request's candidate map.
7. Extend the PocketBase example schema and TypeScript model with `videoEnd`, `videoChannel`,
   `speechSegmentId`, caption provenance, and matched surface/lemma as justified by the article UI.
   Provide a migration suitable for Acervo's current development-stage compatibility needs.
8. Open a host-owned modal from article examples, fetch the current clip by stable segment ID, and
   render `SpeechClipPlayer`. If the service/segment is unavailable, use stored source text and
   `videoRef`/start/end for a direct playback link.
9. Exercise generation, ID validation, persistence, page reload, modal playback, optional
   translation/cancellation, service outage fallback, and channel/source attribution end to end.

## Public interfaces and data

- Release artifacts are the Python wheel/sdist, npm tarball, OpenAPI document, optional external
  model references, and human-readable experiment/benchmark reports.
- Acervo stores stable `speechSegmentId`, `videoRef`, `videoTitle`, `videoStart`, `videoEnd`,
  `videoChannel`, source language/text, matched form, and caption provenance needed for fallback and
  audit.
- Service process ownership, restart, persistent volume, URL, and credentials are host
  configuration; the Python package does not self-daemonize.

## Acceptance tests and verification

- A clean clone on supported Python/Node versions follows the README to test, build, create a
  synthetic corpus, serve it, search it, and open the demo without private files or model downloads.
- Wheel and npm consumer smokes import only documented public entry points and pass against the
  checked-in OpenAPI contract.
- The Acervo checklist is completed in its repository with a migration and end-to-end tests; an LLM
  cannot persist a segment ID that was not in the bounded candidate set.
- The article remains readable and offers a source fallback when retrieval, alignment, translation,
  or optional models are unavailable.
- Release notes state implemented capabilities, benchmark scope, known limitations, artifact/data
  licenses, and reproducible verification commands.

## Non-goals

- Coupling repository release cycles, moving article generation into this service, or making this
  package own Acervo's UI/process lifecycle.
- Public hosting infrastructure, billing, end-user authentication, or a generalized plugin system.
- Requiring every experimental model to pass its promotion gate before the baseline packages can be
  released; rejected experiments remain useful documented results.
