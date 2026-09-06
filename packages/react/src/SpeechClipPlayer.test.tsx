import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  HighlightedSourceText,
  ProgressiveSourceText,
  SpeechClipPlayer,
} from "./SpeechClipPlayer.js";
import { fixtureResult as clip } from "./fixtures.js";
import type { YouTubeNamespace, YouTubePlayer } from "./youtube.js";

class MockPlayer implements YouTubePlayer {
  static latest: MockPlayer;
  static autoPrime = true;
  current = clip.clip_start;
  options: ConstructorParameters<YouTubeNamespace["Player"]>[1];
  playVideo = vi.fn(() => this.options.events.onStateChange({ data: 1 }));
  pauseVideo = vi.fn(() => this.options.events.onStateChange({ data: 2 }));
  loadVideoById = vi.fn((options: { videoId: string; startSeconds: number }) => {
    this.current = options.startSeconds + .08;
    if (MockPlayer.autoPrime) this.options.events.onStateChange({ data: 1 });
  });
  seekTo = vi.fn((seconds: number) => { this.current = seconds; });
  getCurrentTime = vi.fn(() => this.current);
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
