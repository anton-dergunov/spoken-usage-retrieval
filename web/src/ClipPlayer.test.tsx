import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClipPlayer from "./ClipPlayer";
import type { SearchResult } from "./types";
import type { YouTubePlayer } from "./youtube";

const result: SearchResult = {
  occurrence_id: "occurrence-1",
  sentence: "A mí me da mucha bronca cuando pasa eso.",
  match: { text: "bronca", char_start: 17, char_end: 23, accent_exact: true },
  sentence_start: 77.2,
  sentence_end: 82.4,
  clip_start: 76.85,
  clip_end: 83.05,
  boundary: { reason: "punctuation", confidence: 1 },
  quality_score: .94,
  video: {
    provider: "youtube", id: "abc123", url: "https://youtube.test/abc123",
    title: "A conversation", channel_id: "channel", channel: "Easy Spanish",
    varieties: ["Mexico"], speech_style: ["conversation"], duration: 120,
    thumbnail: null, caption_kind: "manual", caption_language: "es",
  },
};

class MockPlayer implements YouTubePlayer {
  static latest: MockPlayer;
  current = result.clip_start;
  options: ConstructorParameters<NonNullable<typeof window.YT>["Player"]>[1];
  playVideo = vi.fn(() => this.options.events.onStateChange({ data: 1 }));
  pauseVideo = vi.fn(() => this.options.events.onStateChange({ data: 2 }));
  seekTo = vi.fn((seconds: number) => { this.current = seconds; });
  getCurrentTime = vi.fn(() => this.current);
  cueVideoById = vi.fn();
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
    window.YT = {
      Player: MockPlayer as NonNullable<typeof window.YT>["Player"],
      PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 },
    };
  });
  afterEach(() => {
    delete window.YT;
    vi.restoreAllMocks();
  });

  it("cues only the excerpt and exposes custom playback controls", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(MockPlayer.latest.cueVideoById).toHaveBeenCalled());
    expect(MockPlayer.latest.cueVideoById).toHaveBeenCalledWith({
      videoId: "abc123", startSeconds: 76.85, endSeconds: 83.05,
    });
    expect(MockPlayer.latest.setOption).toHaveBeenCalledWith("captions", "track", {});
    expect(MockPlayer.latest.options.playerVars).toMatchObject({
      controls: 0, cc_load_policy: 0, disablekb: 1, fs: 0, iv_load_policy: 3,
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Play excerpt" })[1]);
    expect(MockPlayer.latest.playVideo).toHaveBeenCalled();
    expect(await screen.findAllByRole("button", { name: "Pause excerpt" })).not.toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Pause excerpt" }));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalled();
  });

  it("keeps scrubbing inside the clip and returns to its start", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(MockPlayer.latest.cueVideoById).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Excerpt position"), { target: { value: "3" } });
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(79.85, true);
    fireEvent.click(screen.getByRole("button", { name: "Return to excerpt start" }));
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(76.85, true);
    await waitFor(() => expect(screen.getByText("0:00", { exact: false })).toBeInTheDocument());
  });

  it("returns to the clip start when YouTube reaches the excerpt end", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(MockPlayer.latest.cueVideoById).toHaveBeenCalled());
    act(() => MockPlayer.latest.options.events.onStateChange({ data: 0 }));
    expect(MockPlayer.latest.pauseVideo).toHaveBeenCalled();
    expect(MockPlayer.latest.seekTo).toHaveBeenLastCalledWith(result.clip_start, true);
    expect(screen.getByRole("button", { name: "Replay excerpt" })).toBeInTheDocument();
  });

  it("covers YouTube chrome whenever playback is idle", async () => {
    const { container } = render(<ClipPlayer result={result} />);
    await waitFor(() => expect(MockPlayer.latest.cueVideoById).toHaveBeenCalled());
    expect(container.querySelector(".player-poster")).toBeInTheDocument();

    act(() => MockPlayer.latest.options.events.onStateChange({ data: 1 }));
    expect(container.querySelector(".player-poster")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause video" })).toBeInTheDocument();

    act(() => MockPlayer.latest.options.events.onStateChange({ data: 2 }));
    expect(container.querySelector(".player-poster")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause video" })).not.toBeInTheDocument();
  });

  it("shows a timestamped fallback when embedding is disabled", async () => {
    render(<ClipPlayer result={result} />);
    await waitFor(() => expect(MockPlayer.latest.cueVideoById).toHaveBeenCalled());
    act(() => MockPlayer.latest.options.events.onError({ data: 101 }));
    expect(screen.getByText("This video does not allow embedded playback.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open this moment on YouTube ↗" })).toHaveAttribute(
      "href", "https://www.youtube.com/watch?v=abc123&t=76s"
    );
  });
});
