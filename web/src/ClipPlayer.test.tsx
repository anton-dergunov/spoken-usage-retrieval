import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClipPlayer from "./ClipPlayer";
import type { SearchResult } from "./types";
import type { YouTubePlayer } from "./youtube";

const result: SearchResult = {
  occurrence_id: "occurrence-1",
  segment_id: "segment-1",
  source_language: "es",
  sentence: "A mí me da mucha bronca cuando pasa eso.",
  match: { text: "bronca", char_start: 17, char_end: 23, accent_exact: true },
  sentence_start: 77.2,
  sentence_end: 82.4,
  clip_start: 76.85,
  clip_end: 83.05,
  segments: [
    { text: "A mí me da", start: 77.2, end: 78.5, char_start: 0, char_end: 10 },
    { text: "mucha bronca", start: 78.6, end: 80.1, char_start: 11, char_end: 23 },
    { text: "cuando pasa eso.", start: 80.2, end: 82.4, char_start: 24, char_end: 40 },
  ],
  boundary: { reason: "punctuation", confidence: 1 },
  quality_score: .94,
  video: {
    video_key: "video-1", provider: "youtube", id: "abc123", url: "https://youtube.test/abc123",
    title: "A conversation", channel_id: "channel", channel: "Easy Spanish",
    source_language: "es",
    varieties: ["Mexico"], speech_style: ["conversation"], duration: 120,
    thumbnail: null, track_id: "track-1", caption_kind: "manual", caption_language: "es",
  },
};

class MockPlayer implements YouTubePlayer {
  static latest: MockPlayer;
  static autoPrime = true;
  current = result.clip_start;
  options: ConstructorParameters<NonNullable<typeof window.YT>["Player"]>[1];
  playVideo = vi.fn(() => this.options.events.onStateChange({ data: 1 }));
  pauseVideo = vi.fn(() => this.options.events.onStateChange({ data: 2 }));
  loadVideoById = vi.fn((options: { videoId: string; startSeconds: number }) => {
    this.current = options.startSeconds + .08;
    if (MockPlayer.autoPrime) this.options.events.onStateChange({ data: 1 });
  });
  seekTo = vi.fn((seconds: number) => { this.current = seconds; });
  getCurrentTime = vi.fn(() => this.current);
  cueVideoById = vi.fn();
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

describe("ClipPlayer", () => {
  beforeEach(() => {
    MockPlayer.autoPrime = true;
    window.YT = {
      Player: MockPlayer as NonNullable<typeof window.YT>["Player"],
      PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 },
    };
  });
  afterEach(() => {
    vi.useRealTimers();
    delete window.YT;
    vi.restoreAllMocks();
  });

  it("primes a real opening frame muted and exposes custom playback controls", async () => {
    const { container } = render(<ClipPlayer result={result} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    expect(MockPlayer.latest.mute).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.loadVideoById).toHaveBeenCalledWith({
      videoId: "abc123", startSeconds: 76.85,
    });
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.seekTo).not.toHaveBeenCalled();
    expect(MockPlayer.latest.setOption).toHaveBeenCalledWith("captions", "track", {});
    expect(MockPlayer.latest.options.playerVars).toMatchObject({
      controls: 0, cc_load_policy: 0, disablekb: 1, fs: 0, iv_load_policy: 3,
    });
    expect(container.querySelector(".player-poster")).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain("i.ytimg.com");
    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    expect(MockPlayer.latest.unMute).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.playVideo).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Pause video" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pause excerpt" }));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalled();
  });

  it("keeps scrubbing inside the clip and returns to its start", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Excerpt position"), { target: { value: "3" } });
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(79.85, true);
    fireEvent.click(screen.getByRole("button", { name: "Return to excerpt start" }));
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(76.85, true);
    await waitFor(() => expect(screen.getByText("0:00", { exact: false })).toBeInTheDocument());
  });

  it("reveals timed source-caption segments as playback reaches them", async () => {
    vi.useFakeTimers();
    const { container } = render(<ClipPlayer result={result} />);
    await act(async () => {});
    expect(container.querySelectorAll(".timed-fragment.upcoming")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    MockPlayer.latest.current = 79;
    act(() => vi.advanceTimersByTime(120));

    expect(container.querySelectorAll(".timed-fragment.spoken")).toHaveLength(3);
    expect(container.querySelectorAll(".timed-fragment.upcoming")).toHaveLength(1);
  });

  it("does not apply progressive coloring to a single untimed phrase", async () => {
    const untimed = {
      ...result,
      segments: [{
        text: result.sentence, start: result.sentence_start, end: result.sentence_end,
        char_start: 0, char_end: result.sentence.length,
      }],
    };
    const { container } = render(<ClipPlayer result={untimed} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    expect(container.querySelector(".timed-fragment")).not.toBeInTheDocument();
    expect(screen.getByText("bronca")).toBeInTheDocument();
  });

  it("holds the terminal frame and seeks to the start only when replayed", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    MockPlayer.latest.seekTo.mockClear();
    MockPlayer.latest.current = result.clip_end - .08;
    act(() => MockPlayer.latest.options.events.onStateChange({ data: 0 }));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalled();
    expect(MockPlayer.latest.seekTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Replay excerpt" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replay excerpt" }));
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(result.clip_start, true);
    expect(MockPlayer.latest.playVideo).toHaveBeenCalled();
  });

  it("pauses before the clip boundary without replacing the terminal frame", async () => {
    vi.useFakeTimers();
    render(<ClipPlayer result={result} />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    MockPlayer.latest.pauseVideo.mockClear();
    MockPlayer.latest.seekTo.mockClear();
    MockPlayer.latest.current = result.clip_end - .04;

    act(() => vi.advanceTimersByTime(120));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.seekTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Replay excerpt" })).toBeInTheDocument();
  });

  it("keeps the iframe visible and paused at the current frame", async () => {
    const { container } = render(<ClipPlayer result={result} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    expect(container.querySelector(".player-poster")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Play excerpt" }));
    expect(screen.getByRole("button", { name: "Pause video" })).toBeInTheDocument();

    MockPlayer.latest.current = 79.4;
    MockPlayer.latest.seekTo.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Pause video" }));
    expect(MockPlayer.latest.seekTo).not.toHaveBeenCalled();
    expect(container.querySelector(".player-cover")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument();
  });

  it("offers interaction without exposing a thumbnail when muted priming is blocked", async () => {
    vi.useFakeTimers();
    MockPlayer.autoPrime = false;
    const { container } = render(<ClipPlayer result={result} />);
    await act(async () => {});
    expect(MockPlayer.latest.loadVideoById).toHaveBeenCalledOnce();

    act(() => vi.advanceTimersByTime(2600));
    const fallback = screen.getByRole("button", { name: "Load and play excerpt" });
    expect(fallback).toBeInTheDocument();
    expect(container.querySelector(".player-poster")).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain("i.ytimg.com");

    fireEvent.click(fallback);
    expect(MockPlayer.latest.unMute).toHaveBeenCalledOnce();
    expect(MockPlayer.latest.loadVideoById).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Loading excerpt…")).toBeInTheDocument();
    expect(container.querySelector(".loading-cover")).toBeInTheDocument();
  });

  it("shows a timestamped fallback when embedding is disabled", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Play video" })).toBeInTheDocument());
    act(() => MockPlayer.latest.options.events.onError({ data: 101 }));
    expect(screen.getByText("This video does not allow embedded playback.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open this moment on YouTube ↗" })).toHaveAttribute(
      "href", "https://www.youtube.com/watch?v=abc123&t=76s"
    );
  });
});
