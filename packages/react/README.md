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

The transport provides one-tap shortcuts for 0.5×, 0.75×, 1×, and 1.5× plus a selector for
every rate the current YouTube video supports between 0.25× and 2×. Use `defaultPlaybackRate` for
uncontrolled playback, or `playbackRate` with `onPlaybackRateChange` when the host needs to preserve
the preference across player instances:

```tsx
const storageKey = "my-app.playback-rate.v1";
const [playbackRate, setPlaybackRate] = useState<PlaybackRate>(() => {
  const stored = Number(localStorage.getItem(storageKey));
  return isPlaybackRate(stored) ? stored : 1;
});

<SpeechClipPlayer
  clip={clip}
  playbackRate={playbackRate}
  onPlaybackRateChange={(rate) => {
    setPlaybackRate(rate);
    localStorage.setItem(storageKey, String(rate));
  }}
/>;
```

Import `PlaybackRate` and `isPlaybackRate` from the package root. The component itself does not use
storage. YouTube resets playback speed when it loads a video, so the player reapplies the requested
rate once playback begins. If that rate is unavailable for one video, the nearest supported rate is
used for that video without changing the host's saved preference. Arbitrary intermediate values are
not exposed because the YouTube iframe API rounds unsupported rates toward 1×.

`targetLanguage`, `translationStatus`, `targetText`, `translationProvenance`, `alignmentStatus`,
`alignmentGraph`, `alignmentGroups`, `onTranslationRequest`, `onTranslationRetry`, and
`onTranslationCancel` render the optional translation lifecycle. Graph token IDs distinguish
repeated spellings and support many-to-many and noncontiguous links. Hover/focus explores direct
neighbors; click/tap pins a relation until it is tapped again, the background is tapped, or Escape is
pressed. Playback highlights target neighbors of the active source tokens. One whole-sentence timing
unit deliberately produces no automatic target highlight; finer `sourceTiming` works without
regenerating the graph. Authored fallback and valid translations with failed alignment remain static.

The typed client exposes single-clip translation jobs and bounded translation batches. Hosts remain
responsible for polling and for choosing the one active target language.

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
