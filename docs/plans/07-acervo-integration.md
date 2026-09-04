# Plan 07: Acervo integration slice

**Status:** Planned

**Depends on:** Plans 05 and 06

## Outcome

Put the retrieval service into real use inside Acervo: an article can cite an authentic spoken
example, and clicking it opens a working clip player. Source-language playback only, no translation
and no optional models, so the integration seam exists before the research work that improves it.

Completing this plan is the integration checkpoint. Every later plan is an additive upgrade to a
system that is already carrying real traffic, which is the point of doing it here rather than last.

## Current state

- This repository can run its API and demo from source; Plans 05 and 06 make the wheel and npm
  package consumable.
- Acervo is a React/PocketBase application with Compose-managed services and a one-shot worker. Its
  `example` record already stores an explicit language and provenance, and its article model stores
  `videoRef`, `videoTitle`, and `videoStart`, while video opening is not wired into the article
  experience.
- No automated consumer smoke test spans the package boundary.

## Decisions

- Distribute a normal Python wheel and a locally packed npm tarball. Registry publication is a
  release choice, not required to prove integration.
- Run retrieval as its own foreground Compose service in Acervo with a persistent cache/index volume.
  Do not hide a long-running indexer inside Acervo's deliberately one-shot worker command. Corpus
  freshness in this milestone is a scheduled `speech-retrieval update --once`.
- Before article generation, Acervo queries a bounded candidate set for the requested source
  language and vocabulary item. The article LLM may select only IDs from that set, and Acervo
  validates every returned ID before persistence.
- Extend an example with `videoEnd`, `videoChannel`, and `speechSegmentId`, while retaining
  `videoRef`, `videoTitle`, and `videoStart` as direct-playback/display fallback.
- Store source-caption and matched-form provenance with the selected example where it helps audit
  generation. Fetch current timing from the service when opening the player.
- Acervo owns the modal and article navigation; `SpeechClipPlayer` owns clip playback.
- Service health presentation is Acervo's concern. This plan only guarantees that
  `/api/v1/status` and `/api/v1/statistics` expose what such a page needs.

## Implementation work

### Retrieval repository

1. Build the wheel/sdist and npm tarball, inspect their contents, and install both into clean
   temporary consumers using only documented dependencies.
2. Add a minimal cross-package compatibility fixture that exercises search and clip lookup against
   the checked-in OpenAPI contract and the packaged TypeScript client.
3. Document the integration path: service configuration, environment variables, volume layout,
   `update --once` scheduling, and the exact candidate-request shape a host should use.

### Companion Acervo checklist

4. Add a Compose service using the wheel or an image built from it, with an explicit internal base
   URL, health check, cache/index volume, and restart policy owned by Acervo.
5. Add a narrow retrieval client used before article generation. Request ranked candidates by source
   language and headword or phrase, give the LLM stable IDs plus compact context, and validate
   selected IDs against the request's candidate map.
6. Extend the PocketBase example schema and TypeScript model with `videoEnd`, `videoChannel`,
   `speechSegmentId`, caption provenance, and matched surface/lemma as justified by the article UI.
   Provide a migration suitable for Acervo's current development-stage compatibility needs.
7. Open a host-owned modal from article examples, fetch the current clip by stable segment ID, and
   render `SpeechClipPlayer`. If the service or segment is unavailable, use stored source text and
   `videoRef`/start/end for a direct playback link.
8. Exercise generation, ID validation, persistence, page reload, modal playback, service outage
   fallback, and channel attribution end to end.

## Public interfaces and data

- Integration artifacts are the Python wheel/sdist, the npm tarball, and the OpenAPI document.
- Acervo stores stable `speechSegmentId`, `videoRef`, `videoTitle`, `videoStart`, `videoEnd`,
  `videoChannel`, source language/text, matched form, and caption provenance needed for fallback and
  audit.
- Service process ownership, restart, persistent volume, URL, and credentials are host
  configuration; the Python package does not self-daemonize.
- The clip response already carries optional translation and alignment fields, unpopulated here, so
  Plans 08 and 10 need no schema change in Acervo.

## Acceptance tests and verification

- Wheel and npm consumer smokes import only documented public entry points and pass against the
  checked-in OpenAPI contract.
- The Acervo checklist is completed in its repository with a migration and end-to-end tests; an LLM
  cannot persist a segment ID that was not in the bounded candidate set.
- The article remains readable and offers a source fallback when retrieval is unavailable.
- A scheduled `update --once` run against the mounted volume adds new videos without restarting the
  service.
- An article generated end to end cites a clip whose channel attribution and timing match the
  service's current data.

## Non-goals

- Coupling repository release cycles or moving article generation into this service.
- Translation, alignment, audio, ranking models, or the evaluation set; those follow this plan.
- Acervo's cross-service health dashboard, which is tracked in that repository.
- Public hosting infrastructure, billing, or end-user authentication.
