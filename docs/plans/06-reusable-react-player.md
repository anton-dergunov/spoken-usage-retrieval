# Plan 06: Reusable React player

**Status:** Planned

**Depends on:** Plan 05

## Outcome

Extract a locally packable `@spoken-usage-retrieval/react` package that renders and controls a
speech clip but leaves modal, navigation, routing, and persistence ownership to its host. The demo
uses the same package the Acervo integration and the labeling tool will consume.

This milestone packages source-language playback only. Target-language text and progressive
alignment arrive as additive states in Plans 08 and 10.

## Current state

- The React demo owns a custom YouTube excerpt player and search-result UI inside one application.
- Component props, API result types, fetch behavior, and styles are not packaged as a reusable
  contract.
- Source playback works from segment timing; progressive character-level alignment and
  target-language states do not exist yet.

## Decisions

- Publish a player component, not a modal. The host decides when and where it appears and controls
  close/navigation behavior.
- Keep React and React DOM as peer dependencies. Export TypeScript types, a small fetch client, the
  component, and one stylesheet with package-prefixed classes and CSS variables.
- Start with the existing YouTube integration and an explicit media-adapter seam only where the
  current implementation already needs it. Do not build a universal media framework.
- Render source text immediately using cue timing. Define the props for character-level source
  timing and target-language text now, as documented extension points, so Plans 08 and 10 add states
  without breaking the contract.
- The package has two consumers, not one: Plan 07's article modal and Plan 11's labeling tool. The
  labeling tool needs a blind mode that hides rank and score, keyboard-first operation, and a replay
  control, so those belong in the initial prop design rather than being retrofitted.
- Package locally with `npm pack` before considering registry publication or a monorepo build system.

## Implementation work

1. Create the package workspace with a clear export map for `SpeechClipPlayer`, client functions,
   public types, and styles. Keep its build independent of demo-only pages.
2. Define controlled props/callbacks for clip identity/data, playback, source language, status
   changes, recoverable errors, blind presentation, and replay.
3. Move the current excerpt playback logic into the package, retaining bounded seeking, cleanup,
   keyboard control, and fallback link behavior.
4. Add source transcript progression from available timed character groups and stable cue-level
   rendering when finer timing is absent.
5. Implement a typed client for Plan 05 routes with caller-supplied base URL, fetch implementation,
   and operator token only for management calls. Do not hide every API operation behind React state.
6. Make the demo consume the packed/public entry points and add a source-language selector.
7. Document CSS variables, accessibility behavior, minimal integration, and host-modal composition.

## Public interfaces and data

- Export `SpeechClipPlayer`, `SpeechClipPlayerProps`, service response/state types, client creation,
  and package CSS.
- Props reserve `targetText`, `alignmentGroups`, and translation request/cancel callbacks as optional
  from the start; an absent value renders the source-only experience.
- CSS uses a package prefix and documented variables. No global resets, fonts, or host layout rules
  are emitted.

## Acceptance tests and verification

- Component tests cover time-bounded playback, source progression, cue fallback, replay, blind mode,
  keyboard control, recoverable errors, and unmount cleanup.
- Accessibility tests cover an understandable accessible name, keyboard operation, focus-safe
  controls, status announcements, reduced motion, and sufficient default contrast.
- The production demo builds and behaves through package exports rather than private source paths.
- `npm pack` followed by installation into a minimal temporary React consumer type-checks, builds,
  imports CSS, and renders without duplicate React.
- A no-service example still plays a clip from supplied props and exposes a direct source link.

## Non-goals

- Owning a modal, application routing, article state, analytics, a design system, or non-React hosts.
- Supporting every video provider or browser media API before a real integration needs it.
- Implementing translation states or alignment rendering; the props exist, the behavior arrives with
  Plans 08 and 10.
- Publishing to npm in this session.
