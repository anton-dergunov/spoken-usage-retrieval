# Plan 12: Reusable React player

**Status:** Planned

**Depends on:** Plans 10 and 11

## Outcome

Extract a locally packable `@spoken-usage-retrieval/react` package that renders and controls a
speech clip but leaves modal, navigation, routing, and persistence ownership to its host. The demo
uses the same package a future Acervo integration will consume.

## Current state

- The React demo owns a custom YouTube excerpt player and search-result UI inside one application.
- Component props, API result types, fetch behavior, styles, and translation state are not packaged
  as a reusable contract.
- Source playback works from segment timing, but progressive alignment and target-language states do
  not yet exist.

## Decisions

- Publish a player component, not a modal. The host decides when and where it appears and controls
  close/navigation behavior.
- Keep React and React DOM as peer dependencies. Export TypeScript types, a small fetch client, the
  component, and one stylesheet with package-prefixed classes and CSS variables.
- Start with the existing YouTube integration and an explicit media-adapter seam only where the
  current implementation already needs it. Do not build a universal media framework.
- Render source text immediately using cue timing. Enhance progressively with source character
  timing and aligned target highlighting when those capabilities arrive.
- Package locally with `npm pack` before considering registry publication or a monorepo build system.

## Implementation work

1. Create the package workspace with a clear export map for `SpeechClipPlayer`, client functions,
   public types, and styles. Keep its build independent of demo-only pages.
2. Define controlled props/callbacks for clip identity/data, playback, source/target language,
   translation request/cancel/retry, status changes, and recoverable errors.
3. Move the current excerpt playback logic into the package, retaining bounded seeking, cleanup,
   keyboard control, and fallback link behavior.
4. Add source transcript progression from available timed character groups and stable cue-level
   rendering when alignment is absent.
5. Add authored static target text and LLM translation states: unavailable, not requested, loading,
   complete, failed, cancelled, and retryable. Highlight semantically aligned groups without
   implying word-for-word equivalence.
6. Implement a typed client for Plan 10–11 routes with caller-supplied base URL, fetch implementation,
   and operator token only for management calls. Do not hide every API operation behind React state.
7. Make the demo consume the packed/public entry points and add source/target language selectors.
8. Document CSS variables, accessibility behavior, minimal integration, and host-modal composition.

## Public interfaces and data

- Export `SpeechClipPlayer`, `SpeechClipPlayerProps`, service response/state types, client creation,
  and package CSS.
- Request and cancellation callbacks let a host own translation orchestration; a provided service
  client is a convenience, not a requirement.
- CSS uses a package prefix and documented variables. No global resets, fonts, or host layout rules
  are emitted.

## Acceptance tests and verification

- Component tests cover time-bounded playback, source progression, cue fallback, aligned target
  groups, every translation state, request/cancel callbacks, retry, and unmount cleanup.
- Accessibility tests cover an understandable accessible name, keyboard operation, focus-safe
  controls, status announcements, reduced motion, and sufficient default contrast.
- The production demo builds and behaves through package exports rather than private source paths.
- `npm pack` followed by installation into a minimal temporary React consumer type-checks, builds,
  imports CSS, and renders without duplicate React.
- A no-service/no-translation example still plays the source clip and exposes a direct source link.

## Non-goals

- Owning a modal, application routing, article state, analytics, a design system, or non-React hosts.
- Supporting every video provider or browser media API before a real integration needs it.
- Publishing to npm in this session.
