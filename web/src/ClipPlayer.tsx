import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { SearchResult } from "./types";
import { loadYouTubeApi, type YouTubePlayer } from "./youtube";

type PlayerStatus = "connecting" | "ready" | "playing" | "paused" | "buffering" | "replay" | "error";

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
  return <span>
    {result.sentence.slice(0, start)}
    <mark>{result.sentence.slice(start, end)}</mark>
    {result.sentence.slice(end)}
  </span>;
}

export default function ClipPlayer({ result }: { result: SearchResult }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const replayRef = useRef(false);
  const [status, setStatus] = useState<PlayerStatus>("connecting");
  const [current, setCurrent] = useState(result.clip_start);
  const [errorMessage, setErrorMessage] = useState("");
  const clipDuration = Math.max(0.1, result.clip_end - result.clip_start);
  const posterUrl = result.video.thumbnail ?? `https://i.ytimg.com/vi/${result.video.id}/hqdefault.jpg`;

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
    player.pauseVideo();
    player.seekTo(result.clip_start, true);
    setCurrent(result.clip_start);
    setStatus("replay");
  }, [result.clip_start]);

  useEffect(() => {
    let cancelled = false;
    let player: YouTubePlayer | null = null;
    setStatus("connecting");
    setErrorMessage("");
    setCurrent(result.clip_start);
    replayRef.current = false;
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
            target.cueVideoById({
              videoId: result.video.id,
              startSeconds: result.clip_start,
              endSeconds: result.clip_end,
            });
            setCurrent(result.clip_start);
            setStatus("ready");
          },
          onStateChange: ({ data }) => {
            if (data === YT.PlayerState.ENDED) finishClip(player);
            else if (data === YT.PlayerState.PLAYING) setStatus("playing");
            else if (data === YT.PlayerState.BUFFERING) setStatus(replayRef.current ? "replay" : "buffering");
            else if (data === YT.PlayerState.PAUSED) setStatus(replayRef.current ? "replay" : "paused");
            else if (data === YT.PlayerState.CUED) setStatus("ready");
          },
          onError: ({ data }) => {
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
    if (!player || status === "connecting" || status === "error") return;
    if (status === "playing" || status === "buffering") {
      player.pauseVideo();
      return;
    }
    if (replayRef.current) {
      replayRef.current = false;
      player.seekTo(result.clip_start, true);
      setCurrent(result.clip_start);
    }
    player.playVideo();
  }, [result.clip_start, status]);

  const restart = useCallback(() => {
    const player = playerRef.current;
    if (!player) return;
    replayRef.current = false;
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
  const disabled = status === "connecting" || status === "error";
  const showPoster = status === "ready" || status === "paused" || status === "replay";

  return <article className="clip-player" onKeyDown={handleKeys} tabIndex={0} aria-label="Selected video excerpt">
    <div className="player-chrome">
      <div className="video-stage" ref={hostRef} />
      {status === "connecting" && <div className="player-cover loading-cover"><span className="spinner" />Connecting to YouTube…</div>}
      {status === "error" && <div className="player-cover error-cover">
        <span className="error-symbol">!</span>
        <strong>Embedded playback unavailable</strong>
        <span>{errorMessage}</span>
        <a href={youtubeUrl} target="_blank" rel="noreferrer">Open this moment on YouTube ↗</a>
      </div>}
      {showPoster && <div className="player-poster" style={{ backgroundImage: `url("${posterUrl}")` }}>
        <button className="stage-play" onClick={playOrPause}
          aria-label={status === "replay" ? "Replay excerpt" : "Play excerpt"}>
          {status === "replay" ? <RestartIcon /> : <PlayIcon />}
        </button>
      </div>}
      {(status === "playing" || status === "buffering") && <button className="playback-shield"
        onClick={playOrPause} aria-label="Pause video" />}
    </div>

    <div className="transport">
      <button className="transport-primary" onClick={playOrPause} disabled={disabled}
        aria-label={status === "playing" || status === "buffering" ? "Pause excerpt" : "Play excerpt"}>
        <PlayIcon pause={status === "playing" || status === "buffering"} />
      </button>
      <button className="transport-secondary" onClick={restart} disabled={disabled} aria-label="Return to excerpt start">
        <RestartIcon />
      </button>
      <div className="timeline-block">
        <input className="clip-range" type="range" min="0" max={clipDuration} step="0.1"
          value={relativeCurrent} onChange={(event) => seek(Number(event.target.value))}
          disabled={disabled} aria-label="Excerpt position"
          style={{ "--clip-progress": progress } as CSSProperties} />
        <div className="time-row">
          <span>{formatClock(relativeCurrent)} <small>/ {formatClock(clipDuration)}</small></span>
          {status === "buffering" && <span className="buffering-state"><i />Buffering</span>}
        </div>
      </div>
    </div>

    <div className="selected-copy">
      <p className="selected-sentence"><HighlightedSentence result={result} /></p>
      <div className="source-line">
        <span>{result.video.channel}</span>
        <a href={youtubeUrl} target="_blank" rel="noreferrer">{formatClock(result.clip_start)} on YouTube ↗</a>
      </div>
    </div>
  </article>;
}

export { formatClock };
