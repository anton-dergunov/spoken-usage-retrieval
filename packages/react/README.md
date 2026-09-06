# `@spoken-usage-retrieval/react`

A reusable React player and typed HTTP client for
[`spoken-usage-retrieval`](https://github.com/anton-dergunov/spoken-usage-retrieval). The package
renders one source-language speech clip. Its host owns modals, routing, result navigation, and
persistence.

## Install locally

Build and pack the package, then install the resulting tarball in a React application:

```bash
npm ci --prefix packages/react
npm --prefix packages/react pack
npm install ./packages/react/spoken-usage-retrieval-react-0.1.0.tgz
```

React and React DOM are peer dependencies, so the host's React instance is reused.

## Minimal player

```tsx
import { SpeechClipPlayer, type SpeechClip } from "@spoken-usage-retrieval/react";
import "@spoken-usage-retrieval/react/styles.css";

export function Clip({ clip }: { clip: SpeechClip }) {
  return <SpeechClipPlayer clip={clip} />;
}
```

`clip` may be either a `SpeechClip` returned by `GET /clips/{segment_id}` or a `SearchResult`.
No service is required when the host already has that data. Both forms include the direct media URL,
which remains available if embedded playback fails.

The component does not create a dialog. A host can compose one without coupling player state to
modal state:

```tsx
<dialog open={selectedClip !== null} onClose={closeClip} aria-label="Spoken example">
  {selectedClip && (
    <SpeechClipPlayer
      clip={selectedClip}
      playing={playing}
      onPlayingChange={setPlaying}
      onStatusChange={setPlayerStatus}
      onError={reportPlayerError}
    />
  )}
</dialog>
```

`blind` omits rank and score for evaluation sessions. `showReplayControl` and `onReplay` support
keyboard-first labeling flows. Space or K toggles playback, R replays from the start, and the arrow
keys seek by one second when the player itself is focused. Buttons and the range input retain their
native keyboard behavior. Status changes are announced through a polite live region, errors use an
alert, and package motion is effectively disabled when the user requests reduced motion.

`targetText`, `alignmentGroups`, `onTranslationRequest`, and `onTranslationCancel` are reserved
extension points. Version 0.1 deliberately renders source text only.

## Client

```ts
import { createSpeechRetrievalClient } from "@spoken-usage-retrieval/react/client";

const speech = createSpeechRetrievalClient({
  baseUrl: "https://speech.example/api/v1",
  fetch: window.fetch.bind(window), // optional when global fetch is available
});

const results = await speech.search({ query: "la verdad", language: "es" });
const clip = await speech.clip(results.results[0].segment_id);
```

The client exposes search, suggestions, clip lookup, status, statistics, health, channel listing,
and channel management. An optional `operatorToken` is sent only by `addChannel`, `updateChannel`, and
`setChannelEnabled`; it is never attached to read requests. Service failures throw
`SpeechRetrievalApiError` with `status`, `code`, `requestId`, and `details`.

## Styling

The stylesheet has no reset, fonts, or host layout rules. All selectors use the `sur-player` prefix.
Override these variables on `.sur-player` after importing the package stylesheet:

| Variable | Purpose |
| --- | --- |
| `--sur-player-surface`, `--sur-player-surface-muted` | Control and transcript surfaces |
| `--sur-player-text`, `--sur-player-text-muted`, `--sur-player-text-subtle` | Text hierarchy |
| `--sur-player-border` | Borders and inactive timeline |
| `--sur-player-accent`, `--sur-player-accent-hover` | Controls, links, and focus |
| `--sur-player-mark-bg`, `--sur-player-mark-text` | Search-match highlight |
| `--sur-player-unspoken` | Timed text that has not played yet |
| `--sur-player-stage` | Media-stage background |
| `--sur-player-serif`, `--sur-player-sans`, `--sur-player-mono` | Host-provided font stacks |
| `--sur-player-shadow` | Transcript and stage elevation |

The defaults meet WCAG AA contrast for normal text on their paired surfaces. Hosts that override
colors are responsible for preserving sufficient contrast.
