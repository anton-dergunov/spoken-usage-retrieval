import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  HighlightedSourceText,
  PLAYBACK_RATES,
  ProgressiveSourceText,
  ProgressiveTargetText,
  SpeechClipPlayer,
} from "./SpeechClipPlayer.js";
import { fixtureResult as clip } from "./fixtures.js";
import type { YouTubeNamespace, YouTubePlayer } from "./youtube.js";

class MockPlayer implements YouTubePlayer {
  static latest: MockPlayer;
  static autoPrime = true;
  static availablePlaybackRates: number[] = [...PLAYBACK_RATES];
  current = clip.clip_start;
  playbackRate = 1;
  options: ConstructorParameters<YouTubeNamespace["Player"]>[1];
  playVideo = vi.fn(() => this.options.events.onStateChange({ data: 1 }));
  pauseVideo = vi.fn(() => this.options.events.onStateChange({ data: 2 }));
  loadVideoById = vi.fn((options: { videoId: string; startSeconds: number }) => {
    this.current = options.startSeconds + .08;
    this.playbackRate = 1;
    if (MockPlayer.autoPrime) this.options.events.onStateChange({ data: 1 });
  });
  seekTo = vi.fn((seconds: number) => { this.current = seconds; });
  getCurrentTime = vi.fn(() => this.current);
  getPlaybackRate = vi.fn(() => this.playbackRate);
  getAvailablePlaybackRates = vi.fn(() => MockPlayer.availablePlaybackRates);
  setPlaybackRate = vi.fn((rate: number) => {
    if (!MockPlayer.availablePlaybackRates.includes(rate) || this.playbackRate === rate) return;
    this.playbackRate = rate;
    this.options.events.onPlaybackRateChange?.({ data: rate });
  });
  mute = vi.fn();
  unMute = vi.fn();
  setOption = vi.fn();
  destroy = vi.fn();

  constructor(_element: HTMLElement, options: MockPlayer["options"]) {
    this.options = options;
    MockPlayer.latest = this;
    queueMicrotask(() => options.events.onReady({ target: this }));
  }
}

const namespace: YouTubeNamespace = {
  Player: MockPlayer as YouTubeNamespace["Player"],
  PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3 },
};
const loader = vi.fn(() => Promise.resolve(namespace));

describe("SpeechClipPlayer", () => {
  beforeEach(() => {
    MockPlayer.autoPrime = true;
    MockPlayer.availablePlaybackRates = [...PLAYBACK_RATES];
    loader.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("bounds playback, seeking, and replay to the supplied clip", async () => {
    render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    expect(MockPlayer.latest.mute).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.loadVideoById).toHaveBeenCalledWith({ videoId: "abc123", startSeconds: 76.85 });
    expect(MockPlayer.latest.options.playerVars).toMatchObject({ start: 76, end: 84, controls: 0, disablekb: 1 });

    fireEvent.change(screen.getByLabelText("Excerpt position"), { target: { value: "3" } });
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(79.85, true);
    fireEvent.click(screen.getByRole("button", { name: "Replay from excerpt start" }));
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(76.85, true);

    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    MockPlayer.latest.seekTo.mockClear();
    MockPlayer.latest.current = clip.clip_end - .02;
    act(() => MockPlayer.latest.options.events.onStateChange({ data: 0 }));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalled();
    expect(MockPlayer.latest.seekTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Replay excerpt" })).toBeInTheDocument();
  });

  it("progresses timed character groups and falls back to stable cue text", async () => {
    vi.useFakeTimers();
    const { container, rerender } = render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} />);
    await act(async () => {});
    expect(container.querySelectorAll(".sur-player__timed-fragment--upcoming")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    MockPlayer.latest.current = 79;
    act(() => vi.advanceTimersByTime(120));
    expect(container.querySelectorAll(".sur-player__timed-fragment--spoken")).toHaveLength(3);

    const untimed = { ...clip, segments: [{ text: clip.sentence, start: 77.2, end: 82.4, char_start: 0, char_end: 40 }] };
    rerender(<SpeechClipPlayer clip={untimed} youtubeApiLoader={loader} />);
    expect(container.querySelector(".sur-player__timed-fragment")).not.toBeInTheDocument();
    expect(screen.getByText("bronca")).toBeInTheDocument();
  });

  it("supports keyboard-first play, bounded seek, and replay", async () => {
    const onReplay = vi.fn();
    render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} onReplay={onReplay} />);
    const player = await screen.findByRole("article", { name: "Speech clip from Easy Spanish" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    fireEvent.keyDown(player, { code: "Space", key: " " });
    expect(MockPlayer.latest.playVideo).toHaveBeenCalled();
    fireEvent.keyDown(player, { key: "ArrowLeft" });
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(76.85, true);
    fireEvent.keyDown(player, { key: "r" });
    expect(onReplay).toHaveBeenCalledOnce();
    for (const control of screen.getAllByRole("button")) expect(control.tabIndex).toBeGreaterThanOrEqual(0);
  });

  it("honors controlled playback and reports statuses", async () => {
    const statuses: string[] = [];
    const { rerender } = render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} playing={false} onStatusChange={(status) => statuses.push(status)} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    rerender(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} playing onStatusChange={(status) => statuses.push(status)} />);
    await waitFor(() => expect(MockPlayer.latest.playVideo).toHaveBeenCalled());
    expect(statuses).toContain("ready");
    expect(statuses).toContain("playing");
    expect(screen.getByRole("status")).toHaveTextContent("Excerpt playing");
  });

  it("offers quick and complete supported playback-rate controls", async () => {
    const onPlaybackRateChange = vi.fn();
    render(<SpeechClipPlayer
      clip={clip}
      youtubeApiLoader={loader}
      defaultPlaybackRate={0.75}
      onPlaybackRateChange={onPlaybackRateChange}
    />);
    await waitFor(() => expect(screen.getByLabelText("Playback speed")).toHaveValue("0.75"));
    expect(MockPlayer.latest.setPlaybackRate).toHaveBeenCalledWith(0.75);
    expect(screen.getByRole("button", { name: "Set playback speed to 0.75×" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(
      PLAYBACK_RATES.map((rate) => `${rate}×`),
    );

    fireEvent.click(screen.getByRole("button", { name: "Set playback speed to 1.5×" }));
    expect(onPlaybackRateChange).toHaveBeenLastCalledWith(1.5);
    expect(MockPlayer.latest.setPlaybackRate).toHaveBeenLastCalledWith(1.5);
    expect(screen.getByLabelText("Playback speed")).toHaveValue("1.5");

    fireEvent.change(screen.getByLabelText("Playback speed"), { target: { value: "2" } });
    expect(onPlaybackRateChange).toHaveBeenLastCalledWith(2);
    expect(MockPlayer.latest.setPlaybackRate).toHaveBeenLastCalledWith(2);
  });

  it("uses the closest video-supported rate without replacing the requested preference", async () => {
    MockPlayer.availablePlaybackRates = [0.5, 1, 1.5];
    const onPlaybackRateChange = vi.fn();
    render(<SpeechClipPlayer
      clip={clip}
      youtubeApiLoader={loader}
      playbackRate={0.25}
      onPlaybackRateChange={onPlaybackRateChange}
    />);
    await waitFor(() => expect(screen.getByLabelText("Playback speed")).toHaveValue("0.5"));
    expect(MockPlayer.latest.setPlaybackRate).toHaveBeenCalledWith(0.5);
    expect(onPlaybackRateChange).not.toHaveBeenCalled();
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["0.5×", "1×", "1.5×"]);
    expect(screen.getByRole("button", { name: "Set playback speed to 0.75×" })).toBeDisabled();
  });

  it("reapplies the preferred rate when YouTube resets a loaded video to normal speed", async () => {
    render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} playbackRate={0.75} />);
    await waitFor(() => expect(MockPlayer.latest.playbackRate).toBe(0.75));
    MockPlayer.latest.setPlaybackRate.mockClear();

    act(() => MockPlayer.latest.loadVideoById({ videoId: clip.video.id, startSeconds: clip.clip_start }));

    expect(MockPlayer.latest.setPlaybackRate).toHaveBeenCalledWith(0.75);
    expect(MockPlayer.latest.playbackRate).toBe(0.75);
  });

  it("hides evaluation metadata in blind mode", async () => {
    const { rerender } = render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} />);
    await screen.findByText("Rank 1 · Score 0.930");
    rerender(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} blind />);
    expect(screen.queryByText(/Rank 1/)).not.toBeInTheDocument();
  });

  it("surfaces recoverable errors, a retry, and a direct source link", async () => {
    const failure = vi.fn(() => Promise.reject(new Error("Network blocked")));
    const onError = vi.fn();
    render(<SpeechClipPlayer clip={clip} youtubeApiLoader={failure} onError={onError} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Network blocked");
    expect(screen.getByRole("button", { name: "Retry embedded player" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open this moment at the source ↗" })).toHaveAttribute(
      "href", "https://www.youtube.com/watch?v=abc123&t=76s",
    );
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: "connection-error", recoverable: true }));
    fireEvent.click(screen.getByRole("button", { name: "Retry embedded player" }));
    await waitFor(() => expect(failure).toHaveBeenCalledTimes(2));
  });

  it("destroys the media player on unmount", async () => {
    const { unmount } = render(<SpeechClipPlayer clip={clip} youtubeApiLoader={loader} />);
    await waitFor(() => expect(MockPlayer.latest.mute).toHaveBeenCalled());
    const instance = MockPlayer.latest;
    unmount();
    expect(instance.destroy).toHaveBeenCalledOnce();
  });
});

it("uses Unicode character offsets for highlights", () => {
  const text = "🙂 casas bonitas";
  const match = { text: "casas", char_start: 2, char_end: 7, accent_exact: true };
  const timing = [
    { text: "🙂 casas", char_start: 0, char_end: 7, start: 1, end: 2 },
    { text: " bonitas", char_start: 7, char_end: 15, start: 2, end: 3 },
  ];
  const { container, rerender } = render(<HighlightedSourceText text={text} match={match} />);
  expect(container.querySelector("mark")).toHaveTextContent("casas");
  rerender(<ProgressiveSourceText text={text} match={match} timing={timing} currentTime={1.5} />);
  expect(container.textContent).toBe(text);
});

it("highlights target ranges aligned to the source group active now", () => {
  const groups = [
    { group_id: 1, source_ranges: [{ start: 0, end: 10 }], target_ranges: [{ start: 4, end: 7 }] },
    { group_id: 2, source_ranges: [{ start: 11, end: 23 }], target_ranges: [{ start: 0, end: 3 }] },
  ];
  const { container } = render(<ProgressiveTargetText
    sourceText={clip.sentence}
    text="dos uno"
    groups={groups}
    timing={clip.segments}
    currentTime={79}
  />);
  expect(container.querySelector(".sur-player__target-fragment--active")).toHaveTextContent("dos");
});

it("does not animate a translation when timing has no internal lexical boundary", () => {
  const { container } = render(<ProgressiveTargetText
    sourceText="¿Por qué no funciona?"
    text="Why isn't it working?"
    groups={[{
      group_id: 1,
      source_ranges: [{ start: 0, end: 21 }],
      target_ranges: [{ start: 0, end: 21 }],
    }]}
    timing={[{ text: "¿Por qué no funciona?", start: 1, end: 3, char_start: 0, char_end: 21 }]}
    currentTime={2}
  />);
  expect(container).toHaveTextContent("Why isn't it working?");
  expect(container.querySelector(".sur-player__target-fragment--active")).not.toBeInTheDocument();
});

it("links repeated occurrences by token ID and supports pinning", async () => {
  const graph = {
    source_tokens: [
      { id: "S1", text: "Disfrutemos", range: { start: 0, end: 11 } },
      { id: "S2", text: "disfrutemos", range: { start: 13, end: 24 } },
    ],
    target_tokens: [
      { id: "T1", text: "Let's", range: { start: 0, end: 5 } },
      { id: "T2", text: "enjoy", range: { start: 6, end: 11 } },
      { id: "T3", text: "let's", range: { start: 13, end: 18 } },
      { id: "T4", text: "enjoy", range: { start: 19, end: 24 } },
    ],
    edges: [
      { source_token_id: "S1", target_token_id: "T1" },
      { source_token_id: "S1", target_token_id: "T2" },
      { source_token_id: "S2", target_token_id: "T3" },
      { source_token_id: "S2", target_token_id: "T4" },
    ],
    unaligned_source_token_ids: [],
    unaligned_target_token_ids: [],
  };
  const repeatedClip = {
    ...clip,
    sentence: "Disfrutemos, disfrutemos",
    segments: [
      { text: "Disfrutemos", start: clip.clip_start, end: 78.5, char_start: 0, char_end: 11 },
      { text: "disfrutemos", start: 78.6, end: 80.1, char_start: 13, char_end: 24 },
    ],
  };
  const { container, rerender } = render(<SpeechClipPlayer
    clip={repeatedClip} youtubeApiLoader={loader} targetLanguage="en"
    targetText="Let's enjoy, let's enjoy" translationStatus="complete"
    translationProvenance="llm" alignmentStatus="complete" alignmentGraph={graph}
  />);
  expect(container.querySelectorAll(".sur-player__alignment-token--target.sur-player__alignment-token--playback"))
    .toHaveLength(2);
  expect(container.querySelector(".sur-player__alignment-token--target.sur-player__timed-fragment--spoken"))
    .not.toBeInTheDocument();
  const sourceOccurrences = screen.getAllByRole("button", { name: /Show translation links for disfrutemos/i });
  fireEvent.mouseEnter(sourceOccurrences[1]);
  expect(container.querySelectorAll(".sur-player__alignment-token--target.sur-player__alignment-token--selected"))
    .toHaveLength(2);
  fireEvent.click(sourceOccurrences[1]);
  fireEvent.mouseLeave(sourceOccurrences[1]);
  expect(container.querySelectorAll(".sur-player__alignment-token--target.sur-player__alignment-token--selected"))
    .toHaveLength(2);
  fireEvent.keyDown(screen.getByRole("article"), { key: "Escape" });
  expect(container.querySelector(".sur-player__alignment-token--selected")).not.toBeInTheDocument();
  rerender(<SpeechClipPlayer
    clip={repeatedClip} youtubeApiLoader={loader} targetLanguage="en"
    targetText="Let's enjoy, let's enjoy" translationStatus="complete"
    translationProvenance="llm" alignmentStatus="complete" alignmentGraph={graph}
    sourceTiming={[{
      text: repeatedClip.sentence,
      start: clip.clip_start,
      end: clip.clip_end,
      char_start: 0,
      char_end: repeatedClip.sentence.length,
    }]}
  />);
  expect(container.querySelector(".sur-player__alignment-token--playback")).not.toBeInTheDocument();
});

it("shows neutral retry controls without rendering backend errors", () => {
  const onRetry = vi.fn();
  const { rerender } = render(<SpeechClipPlayer
    clip={clip} youtubeApiLoader={loader} targetLanguage="en" translationStatus="failed"
    onTranslationRetry={onRetry}
  />);
  expect(screen.getByText("Translation could not be loaded.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry translation" }));
  expect(onRetry).toHaveBeenCalledWith("en");
  rerender(<SpeechClipPlayer
    clip={clip} youtubeApiLoader={loader} targetLanguage="en" targetText="A translation"
    translationStatus="complete" translationProvenance="llm" alignmentStatus="failed"
    onTranslationRetry={onRetry}
  />);
  expect(screen.getByRole("button", { name: "Retry translation links" })).toBeInTheDocument();
});

it("uses the finalized repeat drawing for transport and translation retry controls", async () => {
  const paths = [
    "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",
    "M3 3v5h5",
  ];
  const { container } = render(<SpeechClipPlayer
    clip={clip}
    youtubeApiLoader={loader}
    targetLanguage="en"
    translationStatus="failed"
    onTranslationRetry={() => undefined}
  />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Replay from excerpt start" })).toBeInTheDocument());
  for (const label of ["Replay from excerpt start", "Retry translation"]) {
    const icon = screen.getByRole("button", { name: label }).querySelector(".sur-player__repeat-icon");
    expect(icon).not.toBeNull();
    expect(Array.from(icon?.querySelectorAll("path") ?? []).map((path) => path.getAttribute("d"))).toEqual(paths);
  }
  expect(container).not.toHaveTextContent("↻");
});

it("requests and renders a configured target language", async () => {
  const onTranslationRequest = vi.fn();
  const { container, rerender } = render(<SpeechClipPlayer
    clip={clip}
    youtubeApiLoader={loader}
    targetLanguage="ru"
    translationStatus="not_requested"
    onTranslationRequest={onTranslationRequest}
  />);
  await waitFor(() => expect(onTranslationRequest).toHaveBeenCalledWith("ru"));
  rerender(<SpeechClipPlayer
    clip={clip}
    youtubeApiLoader={loader}
    targetLanguage="ru"
    targetText="Авторские субтитры"
    translationStatus="complete"
    translationProvenance="authored_track"
    alignmentGroups={[{
      group_id: 1,
      source_ranges: [{ start: 0, end: 5 }],
      target_ranges: [{ start: 0, end: 9 }],
    }]}
  />);
  const translation = container.querySelector(".sur-player__translation");
  expect(translation).toHaveAttribute("aria-live", "polite");
  expect(translation).toHaveAttribute("data-provenance", "authored_track");
  expect(container.querySelector(".sur-player__target-fragment")).not.toBeInTheDocument();
});
