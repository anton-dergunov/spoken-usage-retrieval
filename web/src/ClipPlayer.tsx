import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { SearchResult } from "./types";
import { loadYouTubeApi, type YouTubePlayer } from "./youtube";

type PlayerStatus = "connecting" | "priming" | "awaiting" | "starting" | "ready" | "playing" | "paused" | "buffering" | "replay" | "error";
type InternalPause = "prime" | "awaiting" | "finish" | "restart" | null;

const PRIME_TIMEOUT_MS = 2500;

function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function PlayIcon({ pause = false }: { pause?: boolean }) {
  return pause
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z" /></svg>;
}

function RestartIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7v4H3V3h2v2.3A9 9 0 1 1 3.6 15l2-.5A7 7 0 1 0 7 7Z" /></svg>;
}

export function HighlightedSentence({ result }: { result: SearchResult }) {
  const { char_start: start, char_end: end } = result.match;
  const characters = Array.from(result.sentence);
  return <span>
    {characters.slice(0, start).join("")}
    <mark>{characters.slice(start, end).join("")}</mark>
    {characters.slice(end).join("")}
  </span>;
}

export function ProgressiveSentence({ result, current }: { result: SearchResult; current: number }) {
  if (result.segments.length <= 1) return <HighlightedSentence result={result} />;

  const characters = Array.from(result.sentence);
  const renderRange = (rangeStart: number, rangeEnd: number) => {
    const boundaries = new Set([rangeStart, rangeEnd]);
    for (const segment of result.segments) {
      if (segment.char_start > rangeStart && segment.char_start < rangeEnd) boundaries.add(segment.char_start);
      if (segment.char_end > rangeStart && segment.char_end < rangeEnd) boundaries.add(segment.char_end);
    }
    const points = [...boundaries].sort((left, right) => left - right);
    return points.slice(0, -1).map((start, index) => {
      const end = points[index + 1];
      const text = characters.slice(start, end).join("");
      const segment = result.segments.find((item) => start >= item.char_start && end <= item.char_end);
      const timingClass = segment
        ? `timed-fragment ${current >= segment.start ? "spoken" : "upcoming"}`
        : "timed-gap";
      return <span className={timingClass} key={`${start}:${end}`}>{text}</span>;
    });
  };

  return <span aria-label={result.sentence}>
    {renderRange(0, result.match.char_start)}
    <mark>{renderRange(result.match.char_start, result.match.char_end)}</mark>
    {renderRange(result.match.char_end, characters.length)}
  </span>;
}

export default function ClipPlayer({ result }: { result: SearchResult }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const replayRef = useRef(false);
  const primingRef = useRef(false);
  const startingRef = useRef(false);
  const awaitingInteractionRef = useRef(false);
  const internalPauseRef = useRef<InternalPause>(null);
  const [status, setStatus] = useState<PlayerStatus>("connecting");
  const [current, setCurrent] = useState(result.clip_start);
  const [errorMessage, setErrorMessage] = useState("");
  const clipDuration = Math.max(0.1, result.clip_end - result.clip_start);

  const disableYouTubeCaptions = useCallback((player: YouTubePlayer) => {
    try {
      player.setOption?.("captions", "track", {});
    } catch {
      // Caption preferences are controlled by YouTube; keep this best-effort.
    }
  }, []);

  const finishClip = useCallback((player = playerRef.current) => {
    if (!player) return;
    replayRef.current = true;
    internalPauseRef.current = "finish";
    const frameTime = player.getCurrentTime();
    player.pauseVideo();
    setCurrent(Math.min(result.clip_end, Math.max(result.clip_start, frameTime)));
    setStatus("replay");
  }, [result.clip_end, result.clip_start]);

  useEffect(() => {
    let cancelled = false;
    let player: YouTubePlayer | null = null;
    let primeTimer: number | undefined;
    setStatus("connecting");
    setErrorMessage("");
    setCurrent(result.clip_start);
    replayRef.current = false;
    primingRef.current = false;
    startingRef.current = false;
    awaitingInteractionRef.current = false;
    internalPauseRef.current = null;
    hostRef.current?.replaceChildren();
    const mount = document.createElement("div");
    mount.className = "youtube-mount";
    hostRef.current?.appendChild(mount);

    loadYouTubeApi().then((YT) => {
      if (cancelled) return;
      player = new YT.Player(mount, {
        videoId: result.video.id,
        playerVars: {
          controls: 0,
          cc_load_policy: 0,
          disablekb: 1,
          enablejsapi: 1,
          fs: 0,
          iv_load_policy: 3,
          playsinline: 1,
          rel: 0,
          start: Math.floor(result.clip_start),
          end: Math.ceil(result.clip_end),
          origin: window.location.origin,
        },
        events: {
          onReady: ({ target }) => {
            playerRef.current = target;
            disableYouTubeCaptions(target);
            setCurrent(result.clip_start);
            setStatus("priming");
            primingRef.current = true;
            try {
              target.mute();
              target.loadVideoById({
                videoId: result.video.id,
                startSeconds: result.clip_start,
              });
              primeTimer = window.setTimeout(() => {
                if (cancelled || !primingRef.current) return;
                primingRef.current = false;
                awaitingInteractionRef.current = true;
                internalPauseRef.current = "awaiting";
                target.pauseVideo();
                setStatus("awaiting");
              }, PRIME_TIMEOUT_MS);
            } catch {
              primingRef.current = false;
              awaitingInteractionRef.current = true;
              setStatus("awaiting");
            }
          },
          onStateChange: ({ data }) => {
            if (data === YT.PlayerState.ENDED) finishClip(player);
            else if (data === YT.PlayerState.PLAYING) {
              if (primingRef.current) {
                primingRef.current = false;
                if (primeTimer !== undefined) window.clearTimeout(primeTimer);
                const frameTime = player?.getCurrentTime() ?? result.clip_start;
                setCurrent(Math.min(result.clip_end, Math.max(result.clip_start, frameTime)));
                internalPauseRef.current = "prime";
                player?.pauseVideo();
                setStatus("ready");
              } else if (awaitingInteractionRef.current || replayRef.current) {
                internalPauseRef.current = awaitingInteractionRef.current ? "awaiting" : "finish";
                player?.pauseVideo();
                setStatus(awaitingInteractionRef.current ? "awaiting" : "replay");
              } else {
                startingRef.current = false;
                setStatus("playing");
              }
            } else if (data === YT.PlayerState.BUFFERING) {
              if (primingRef.current) setStatus("priming");
              else if (awaitingInteractionRef.current) setStatus("awaiting");
              else if (startingRef.current) setStatus("starting");
              else setStatus(replayRef.current ? "replay" : "buffering");
            } else if (data === YT.PlayerState.PAUSED) {
              const reason = internalPauseRef.current;
              internalPauseRef.current = null;
              if (primingRef.current) setStatus("priming");
              else if (reason === "prime" || reason === "restart") setStatus("ready");
              else if (reason === "awaiting") setStatus("awaiting");
              else if (reason === "finish" || replayRef.current) setStatus("replay");
              else setStatus("paused");
            }
          },
          onError: ({ data }) => {
            if (primeTimer !== undefined) window.clearTimeout(primeTimer);
            primingRef.current = false;
            startingRef.current = false;
            const unavailable = data === 101 || data === 150;
            setErrorMessage(unavailable ? "This video does not allow embedded playback." : `YouTube player error ${data}.`);
            setStatus("error");
          },
          onApiChange: () => {
            if (playerRef.current) disableYouTubeCaptions(playerRef.current);
          },
        },
      });
      playerRef.current = player;
    }).catch((error: Error) => {
      if (!cancelled) {
        setErrorMessage(error.message);
        setStatus("error");
      }
    });

    return () => {
      cancelled = true;
      if (primeTimer !== undefined) window.clearTimeout(primeTimer);
      if (player) player.destroy();
      if (playerRef.current === player) playerRef.current = null;
      hostRef.current?.replaceChildren();
    };
  }, [disableYouTubeCaptions, finishClip, result.clip_end, result.clip_start, result.video.id]);

  useEffect(() => {
    if (status !== "playing") return;
    const interval = window.setInterval(() => {
      const time = playerRef.current?.getCurrentTime();
      if (typeof time !== "number") return;
      if (time >= result.clip_end - 0.05) {
        finishClip();
      } else if (time < result.clip_start - 0.5) {
        playerRef.current?.seekTo(result.clip_start, true);
        setCurrent(result.clip_start);
      } else {
        setCurrent(Math.max(result.clip_start, time));
      }
    }, 100);
    return () => window.clearInterval(interval);
  }, [finishClip, result.clip_end, result.clip_start, status]);

  const playOrPause = useCallback(() => {
    const player = playerRef.current;
    if (!player || status === "connecting" || status === "priming" || status === "starting" || status === "error") return;
    if (status === "playing" || status === "buffering") {
      player.pauseVideo();
      return;
    }
    player.unMute();
    if (status === "awaiting") {
      awaitingInteractionRef.current = false;
      replayRef.current = false;
      startingRef.current = true;
      setCurrent(result.clip_start);
      setStatus("starting");
      player.loadVideoById({ videoId: result.video.id, startSeconds: result.clip_start });
      return;
    }
    if (replayRef.current) {
      replayRef.current = false;
      player.seekTo(result.clip_start, true);
      setCurrent(result.clip_start);
    }
    player.playVideo();
  }, [result.clip_start, result.video.id, status]);

  const restart = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;
    awaitingInteractionRef.current = false;
    startingRef.current = false;
    replayRef.current = false;
    internalPauseRef.current = "restart";
    player.pauseVideo();
    player.seekTo(result.clip_start, true);
    setCurrent(result.clip_start);
    setStatus("ready");
  }, [result.clip_start]);

  const seek = useCallback((relativeSeconds: number) => {
    const absolute = Math.min(result.clip_end, Math.max(result.clip_start, result.clip_start + relativeSeconds));
    replayRef.current = false;
    playerRef.current?.seekTo(absolute, true);
    setCurrent(absolute);
    if (status === "replay") setStatus("paused");
  }, [result.clip_end, result.clip_start, status]);

  const handleKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target instanceof HTMLInputElement) return;
    if (event.code === "Space") {
      event.preventDefault();
      playOrPause();
    } else if (event.key.toLowerCase() === "r") {
      event.preventDefault();
      restart();
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      seek(current - result.clip_start + (event.key === "ArrowLeft" ? -1 : 1));
    }
  };

  const relativeCurrent = Math.min(clipDuration, Math.max(0, current - result.clip_start));
  const progress = `${(relativeCurrent / clipDuration) * 100}%`;
  const youtubeUrl = useMemo(
    () => `https://www.youtube.com/watch?v=${encodeURIComponent(result.video.id)}&t=${Math.floor(result.clip_start)}s`,
    [result.clip_start, result.video.id]
  );
  const disabled = status === "connecting" || status === "priming" || status === "starting" || status === "error";
  const navigationDisabled = disabled || status === "awaiting";
  const active = status === "playing" || status === "buffering";
  const interactive = status === "ready" || status === "playing" || status === "paused" || status === "buffering" || status === "replay";

  return <article className="clip-player" onKeyDown={handleKeys} tabIndex={0} aria-label="Selected video excerpt">
    <div className="player-chrome">
      <div className="video-stage" ref={hostRef} />
      {(status === "connecting" || status === "priming" || status === "starting") && <div className="player-cover loading-cover">
        <span className="spinner" />{status === "starting" ? "Loading excerpt…" : "Preparing excerpt…"}
      </div>}
      {status === "awaiting" && <div className="player-cover awaiting-cover">
        <button className="stage-play" onClick={playOrPause} aria-label="Load and play excerpt"><PlayIcon /></button>
      </div>}
      {status === "error" && <div className="player-cover error-cover">
        <span className="error-symbol">!</span>
        <strong>Embedded playback unavailable</strong>
        <span>{errorMessage}</span>
        <a href={youtubeUrl} target="_blank" rel="noreferrer">Open this moment on YouTube ↗</a>
      </div>}
      {interactive && <button className="playback-shield" onClick={playOrPause}
        aria-label={active ? "Pause video" : status === "replay" ? "Replay excerpt" : "Play video"} />}
    </div>

    <div className="transport">
      <button className="transport-primary" onClick={playOrPause} disabled={disabled}
        aria-label={status === "playing" || status === "buffering" ? "Pause excerpt" : "Play excerpt"}>
        <PlayIcon pause={active} />
      </button>
      <button className="transport-secondary" onClick={restart} disabled={navigationDisabled} aria-label="Return to excerpt start">
        <RestartIcon />
      </button>
      <div className="timeline-block">
        <input className="clip-range" type="range" min="0" max={clipDuration} step="0.1"
          value={relativeCurrent} onChange={(event) => seek(Number(event.target.value))}
          disabled={navigationDisabled} aria-label="Excerpt position"
          style={{ "--clip-progress": progress } as CSSProperties} />
        <div className="time-row">
          <span>{formatClock(relativeCurrent)} <small>/ {formatClock(clipDuration)}</small></span>
          {status === "buffering" && <span className="buffering-state"><i />Buffering</span>}
        </div>
      </div>
    </div>

    <div className="selected-copy">
      <p className="selected-sentence"><ProgressiveSentence result={result} current={current} /></p>
      <div className="source-line">
        <span>{result.video.channel}</span>
        <a href={youtubeUrl} target="_blank" rel="noreferrer">{formatClock(result.clip_start)} on YouTube ↗</a>
      </div>
    </div>
  </article>;
}

export { formatClock };
