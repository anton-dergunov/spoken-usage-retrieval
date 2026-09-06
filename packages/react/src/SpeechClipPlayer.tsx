import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type {
  AlignmentGroup,
  MatchSpan,
  SpeechClipPlayerClip,
  TimedText,
  TranslationState,
} from "./types.js";
import {
  loadYouTubeApi,
  type YouTubeApiLoader,
  type YouTubePlayer,
} from "./youtube.js";

export type SpeechClipPlayerStatus =
  | "connecting"
  | "priming"
  | "awaiting-interaction"
  | "starting"
  | "ready"
  | "playing"
  | "paused"
  | "buffering"
  | "ended"
  | "error";

export interface SpeechClipPlayerError {
  message: string;
  code: "unsupported-provider" | "embed-unavailable" | "player-error" | "connection-error";
  recoverable: boolean;
  sourceUrl: string;
  cause?: unknown;
}

export interface SpeechClipPlayerProps {
  clip: SpeechClipPlayerClip;
  sourceLanguage?: string;
  sourceTiming?: TimedText[];
  playing?: boolean;
  onPlayingChange?: (playing: boolean) => void;
  onStatusChange?: (status: SpeechClipPlayerStatus) => void;
  onTimeChange?: (absoluteSeconds: number) => void;
  onError?: (error: SpeechClipPlayerError) => void;
  blind?: boolean;
  showReplayControl?: boolean;
  onReplay?: () => void;
  targetText?: string | null;
  targetLanguage?: string | null;
  translationStatus?: TranslationState;
  translationError?: string | null;
  translationProvenance?: "llm" | "authored_track" | null;
  alignmentGroups?: AlignmentGroup[] | null;
  onTranslationRequest?: (targetLanguage: string) => void;
  onTranslationCancel?: () => void;
  accessibleName?: string;
  youtubeApiLoader?: YouTubeApiLoader;
}

type InternalPause = "prime" | "awaiting" | "finish" | "restart" | null;

const PRIME_TIMEOUT_MS = 2500;

export function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function clipText(clip: SpeechClipPlayerClip): string {
  return "sentence" in clip ? clip.sentence : clip.source_text;
}

function clipMatch(clip: SpeechClipPlayerClip): MatchSpan | undefined {
  return "match" in clip ? clip.match : undefined;
}

function directSourceUrl(clip: SpeechClipPlayerClip): string {
  if (clip.video.provider === "youtube") {
    return `https://www.youtube.com/watch?v=${encodeURIComponent(clip.video.id)}&t=${Math.floor(clip.clip_start)}s`;
  }
  return clip.video.url;
}

function PlayIcon({ pause = false }: { pause?: boolean }) {
  return pause
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z" /></svg>;
}

function ReplayIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7v4H3V3h2v2.3A9 9 0 1 1 3.6 15l2-.5A7 7 0 1 0 7 7Z" /></svg>;
}

export function HighlightedSourceText({ text, match }: { text: string; match?: MatchSpan }): ReactNode {
  if (!match) return text;
  const characters = Array.from(text);
  const start = Math.max(0, Math.min(characters.length, match.char_start));
  const end = Math.max(start, Math.min(characters.length, match.char_end));
  return <>
    {characters.slice(0, start).join("")}
    <mark className="sur-player__match">{characters.slice(start, end).join("")}</mark>
    {characters.slice(end).join("")}
  </>;
}

export function ProgressiveSourceText({
  text,
  match,
  timing,
  currentTime,
}: {
  text: string;
  match?: MatchSpan;
  timing: TimedText[];
  currentTime: number;
}) {
  if (timing.length <= 1) return <span><HighlightedSourceText text={text} match={match} /></span>;

  const characters = Array.from(text);
  const renderRange = (rangeStart: number, rangeEnd: number) => {
    const boundaries = new Set([rangeStart, rangeEnd]);
    for (const group of timing) {
      if (group.char_start > rangeStart && group.char_start < rangeEnd) boundaries.add(group.char_start);
      if (group.char_end > rangeStart && group.char_end < rangeEnd) boundaries.add(group.char_end);
    }
    const points = [...boundaries].sort((left, right) => left - right);
    return points.slice(0, -1).map((start, index) => {
      const end = points[index + 1];
      const group = timing.find((item) => start >= item.char_start && end <= item.char_end);
      const className = group
        ? `sur-player__timed-fragment sur-player__timed-fragment--${currentTime >= group.start ? "spoken" : "upcoming"}`
        : "sur-player__timed-gap";
      return <span className={className} key={`${start}:${end}`}>{characters.slice(start, end).join("")}</span>;
    });
  };
  const start = Math.max(0, Math.min(characters.length, match?.char_start ?? 0));
  const end = Math.max(start, Math.min(characters.length, match?.char_end ?? 0));

  return <span aria-label={text}>
    {match ? <>
      {renderRange(0, start)}
      <mark className="sur-player__match">{renderRange(start, end)}</mark>
      {renderRange(end, characters.length)}
    </> : renderRange(0, characters.length)}
  </span>;
}

export function ProgressiveTargetText({
  text,
  groups,
  timing,
  currentTime,
}: {
  text: string;
  groups: AlignmentGroup[];
  timing: TimedText[];
  currentTime: number;
}) {
  const characters = Array.from(text);
  const activeSource = timing.filter((item) => currentTime >= item.start && currentTime < item.end);
  const activeRanges = groups.flatMap((group) => {
    const active = group.source_ranges.some((range) => activeSource.some(
      (timed) => range.start < timed.char_end && range.end > timed.char_start,
    ));
    return active ? group.target_ranges : [];
  });
  const boundaries = new Set([0, characters.length]);
  for (const range of activeRanges) {
    boundaries.add(Math.max(0, Math.min(characters.length, range.start)));
    boundaries.add(Math.max(0, Math.min(characters.length, range.end)));
  }
  const points = [...boundaries].sort((left, right) => left - right);
  return <span aria-label={text}>{points.slice(0, -1).map((start, index) => {
    const end = points[index + 1];
    const active = activeRanges.some((range) => start >= range.start && end <= range.end);
    return <span
      className={active ? "sur-player__target-fragment sur-player__target-fragment--active" : "sur-player__target-fragment"}
      key={`${start}:${end}`}
    >{characters.slice(start, end).join("")}</span>;
  })}</span>;
}

function statusMessage(status: SpeechClipPlayerStatus): string {
  switch (status) {
    case "connecting": return "Connecting to media player";
    case "priming": return "Preparing excerpt";
    case "awaiting-interaction": return "Excerpt ready to load";
    case "starting": return "Loading excerpt";
    case "ready": return "Excerpt ready";
    case "playing": return "Excerpt playing";
    case "paused": return "Excerpt paused";
    case "buffering": return "Excerpt buffering";
    case "ended": return "Excerpt finished";
    case "error": return "Embedded playback unavailable";
  }
}

export function SpeechClipPlayer({
  clip,
  sourceLanguage,
  sourceTiming,
  playing,
  onPlayingChange,
  onStatusChange,
  onTimeChange,
  onError,
  blind = false,
  showReplayControl = true,
  onReplay,
  accessibleName,
  youtubeApiLoader = loadYouTubeApi,
  targetText,
  targetLanguage,
  translationStatus = "not_requested",
  translationError,
  translationProvenance,
  alignmentGroups,
  onTranslationRequest,
  onTranslationCancel,
}: SpeechClipPlayerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const replayRef = useRef(false);
  const primingRef = useRef(false);
  const startingRef = useRef(false);
  const awaitingInteractionRef = useRef(false);
  const internalPauseRef = useRef<InternalPause>(null);
  const onPlayingChangeRef = useRef(onPlayingChange);
  const onStatusChangeRef = useRef(onStatusChange);
  const onTimeChangeRef = useRef(onTimeChange);
  const onErrorRef = useRef(onError);
  const [status, setStatus] = useState<SpeechClipPlayerStatus>("connecting");
  const [current, setCurrent] = useState(clip.clip_start);
  const [playerError, setPlayerError] = useState<SpeechClipPlayerError | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const text = clipText(clip);
  const match = clipMatch(clip);
  const timing = sourceTiming ?? clip.segments;
  const duration = Math.max(0.1, clip.clip_end - clip.clip_start);
  const sourceUrl = useMemo(() => directSourceUrl(clip), [clip]);

  useEffect(() => { onPlayingChangeRef.current = onPlayingChange; }, [onPlayingChange]);
  useEffect(() => { onStatusChangeRef.current = onStatusChange; }, [onStatusChange]);
  useEffect(() => { onTimeChangeRef.current = onTimeChange; }, [onTimeChange]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { onStatusChangeRef.current?.(status); }, [status]);
  useEffect(() => { onTimeChangeRef.current?.(current); }, [current]);
  useEffect(() => {
    if (playerError) onErrorRef.current?.(playerError);
  }, [playerError]);
  useEffect(() => {
    if (targetLanguage && !targetText && translationStatus === "not_requested") {
      onTranslationRequest?.(targetLanguage);
    }
  }, [onTranslationRequest, targetLanguage, targetText, translationStatus]);

  const disableYouTubeCaptions = useCallback((playerInstance: YouTubePlayer) => {
    try {
      playerInstance.setOption?.("captions", "track", {});
    } catch {
      // YouTube controls caption preferences; custom source text remains available below.
    }
  }, []);

  const finishClip = useCallback((playerInstance = playerRef.current) => {
    if (!playerInstance) return;
    replayRef.current = true;
    internalPauseRef.current = "finish";
    const frameTime = playerInstance.getCurrentTime();
    playerInstance.pauseVideo();
    setCurrent(Math.min(clip.clip_end, Math.max(clip.clip_start, frameTime)));
    setStatus("ended");
    onPlayingChangeRef.current?.(false);
  }, [clip.clip_end, clip.clip_start]);

  useEffect(() => {
    let cancelled = false;
    let playerInstance: YouTubePlayer | null = null;
    let primeTimer: number | undefined;
    setStatus("connecting");
    setPlayerError(null);
    setCurrent(clip.clip_start);
    replayRef.current = false;
    primingRef.current = false;
    startingRef.current = false;
    awaitingInteractionRef.current = false;
    internalPauseRef.current = null;
    hostRef.current?.replaceChildren();

    if (clip.video.provider !== "youtube") {
      const error: SpeechClipPlayerError = {
        message: `The ${clip.video.provider} provider is not supported for embedded playback.`,
        code: "unsupported-provider",
        recoverable: false,
        sourceUrl,
      };
      setPlayerError(error);
      setStatus("error");
      return;
    }

    const mount = document.createElement("div");
    mount.className = "sur-player__youtube-mount";
    hostRef.current?.appendChild(mount);

    youtubeApiLoader().then((YT) => {
      if (cancelled) return;
      playerInstance = new YT.Player(mount, {
        videoId: clip.video.id,
        playerVars: {
          controls: 0,
          cc_load_policy: 0,
          disablekb: 1,
          enablejsapi: 1,
          fs: 0,
          iv_load_policy: 3,
          playsinline: 1,
          rel: 0,
          start: Math.floor(clip.clip_start),
          end: Math.ceil(clip.clip_end),
          origin: window.location.origin,
        },
        events: {
          onReady: ({ target }) => {
            playerRef.current = target;
            disableYouTubeCaptions(target);
            setCurrent(clip.clip_start);
            setStatus("priming");
            primingRef.current = true;
            try {
              target.mute();
              target.loadVideoById({ videoId: clip.video.id, startSeconds: clip.clip_start });
              primeTimer = window.setTimeout(() => {
                if (cancelled || !primingRef.current) return;
                primingRef.current = false;
                awaitingInteractionRef.current = true;
                internalPauseRef.current = "awaiting";
                target.pauseVideo();
                setStatus("awaiting-interaction");
              }, PRIME_TIMEOUT_MS);
            } catch {
              primingRef.current = false;
              awaitingInteractionRef.current = true;
              setStatus("awaiting-interaction");
              setPlayerError(null);
            }
          },
          onStateChange: ({ data }) => {
            if (data === YT.PlayerState.ENDED) finishClip(playerInstance);
            else if (data === YT.PlayerState.PLAYING) {
              if (primingRef.current) {
                primingRef.current = false;
                if (primeTimer !== undefined) window.clearTimeout(primeTimer);
                const frameTime = playerInstance?.getCurrentTime() ?? clip.clip_start;
                setCurrent(Math.min(clip.clip_end, Math.max(clip.clip_start, frameTime)));
                internalPauseRef.current = "prime";
                playerInstance?.pauseVideo();
                setStatus("ready");
              } else if (awaitingInteractionRef.current || replayRef.current) {
                internalPauseRef.current = awaitingInteractionRef.current ? "awaiting" : "finish";
                playerInstance?.pauseVideo();
                setStatus(awaitingInteractionRef.current ? "awaiting-interaction" : "ended");
              } else {
                startingRef.current = false;
                setStatus("playing");
              }
            } else if (data === YT.PlayerState.BUFFERING) {
              if (primingRef.current) setStatus("priming");
              else if (awaitingInteractionRef.current) setStatus("awaiting-interaction");
              else if (startingRef.current) setStatus("starting");
              else setStatus(replayRef.current ? "ended" : "buffering");
            } else if (data === YT.PlayerState.PAUSED) {
              const reason = internalPauseRef.current;
              internalPauseRef.current = null;
              if (primingRef.current) setStatus("priming");
              else if (reason === "prime" || reason === "restart") setStatus("ready");
              else if (reason === "awaiting") setStatus("awaiting-interaction");
              else if (reason === "finish" || replayRef.current) setStatus("ended");
              else setStatus("paused");
            }
          },
          onError: ({ data }) => {
            if (primeTimer !== undefined) window.clearTimeout(primeTimer);
            primingRef.current = false;
            startingRef.current = false;
            const unavailable = data === 101 || data === 150;
            setPlayerError({
              message: unavailable
                ? "This video does not allow embedded playback."
                : `YouTube player error ${data}.`,
              code: unavailable ? "embed-unavailable" : "player-error",
              recoverable: !unavailable,
              sourceUrl,
            });
            setStatus("error");
          },
          onApiChange: () => {
            if (playerRef.current) disableYouTubeCaptions(playerRef.current);
          },
        },
      });
      playerRef.current = playerInstance;
    }).catch((cause: unknown) => {
      if (cancelled) return;
      setPlayerError({
        message: cause instanceof Error ? cause.message : "Could not connect to YouTube",
        code: "connection-error",
        recoverable: true,
        sourceUrl,
        cause,
      });
      setStatus("error");
    });

    return () => {
      cancelled = true;
      if (primeTimer !== undefined) window.clearTimeout(primeTimer);
      playerInstance?.destroy();
      if (playerRef.current === playerInstance) playerRef.current = null;
      hostRef.current?.replaceChildren();
    };
  }, [
    clip.clip_end,
    clip.clip_start,
    clip.segment_id,
    clip.video.id,
    clip.video.provider,
    disableYouTubeCaptions,
    finishClip,
    loadAttempt,
    sourceUrl,
    youtubeApiLoader,
  ]);

  useEffect(() => {
    if (status !== "playing") return;
    const interval = window.setInterval(() => {
      const time = playerRef.current?.getCurrentTime();
      if (typeof time !== "number") return;
      if (time >= clip.clip_end - 0.05) {
        finishClip();
      } else if (time < clip.clip_start - 0.5) {
        playerRef.current?.seekTo(clip.clip_start, true);
        setCurrent(clip.clip_start);
      } else {
        setCurrent(Math.max(clip.clip_start, time));
      }
    }, 100);
    return () => window.clearInterval(interval);
  }, [clip.clip_end, clip.clip_start, finishClip, status]);

  const requestPlayback = useCallback((shouldPlay: boolean) => {
    const playerInstance = playerRef.current;
    if (!playerInstance || status === "connecting" || status === "priming" || status === "starting" || status === "error") return;
    if (!shouldPlay) {
      if (status === "playing" || status === "buffering") playerInstance.pauseVideo();
      return;
    }
    playerInstance.unMute();
    if (status === "awaiting-interaction") {
      awaitingInteractionRef.current = false;
      replayRef.current = false;
      startingRef.current = true;
      setCurrent(clip.clip_start);
      setStatus("starting");
      playerInstance.loadVideoById({ videoId: clip.video.id, startSeconds: clip.clip_start });
      return;
    }
    if (replayRef.current || status === "ended") {
      replayRef.current = false;
      playerInstance.seekTo(clip.clip_start, true);
      setCurrent(clip.clip_start);
      onReplay?.();
    }
    playerInstance.playVideo();
  }, [clip.clip_start, clip.video.id, onReplay, status]);

  const togglePlayback = useCallback(() => {
    const active = status === "playing" || status === "buffering";
    const next = !active;
    onPlayingChange?.(next);
    requestPlayback(next);
  }, [onPlayingChange, requestPlayback, status]);

  useEffect(() => {
    if (playing === undefined) return;
    const active = status === "playing" || status === "buffering";
    if (playing && !active && status !== "ended") requestPlayback(true);
    else if (!playing && active) requestPlayback(false);
  }, [playing, requestPlayback, status]);

  const replay = useCallback(() => {
    const playerInstance = playerRef.current;
    if (!playerInstance) return;
    awaitingInteractionRef.current = false;
    startingRef.current = false;
    replayRef.current = false;
    internalPauseRef.current = "restart";
    playerInstance.pauseVideo();
    playerInstance.seekTo(clip.clip_start, true);
    setCurrent(clip.clip_start);
    setStatus("ready");
    onPlayingChange?.(false);
    onReplay?.();
  }, [clip.clip_start, onPlayingChange, onReplay]);

  const seek = useCallback((relativeSeconds: number) => {
    const absolute = Math.min(clip.clip_end, Math.max(clip.clip_start, clip.clip_start + relativeSeconds));
    replayRef.current = false;
    playerRef.current?.seekTo(absolute, true);
    setCurrent(absolute);
    if (status === "ended") setStatus("paused");
  }, [clip.clip_end, clip.clip_start, status]);

  const handleKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.code === "Space" || event.key.toLowerCase() === "k") {
      event.preventDefault();
      togglePlayback();
    } else if (event.key.toLowerCase() === "r") {
      event.preventDefault();
      replay();
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      seek(current - clip.clip_start + (event.key === "ArrowLeft" ? -1 : 1));
    }
  };

  const relativeCurrent = Math.min(duration, Math.max(0, current - clip.clip_start));
  const progress = `${(relativeCurrent / duration) * 100}%`;
  const disabled = status === "connecting" || status === "priming" || status === "starting" || status === "error";
  const navigationDisabled = disabled || status === "awaiting-interaction";
  const active = status === "playing" || status === "buffering";
  const interactive = status === "ready" || active || status === "paused" || status === "ended";
  const label = accessibleName ?? `Speech clip from ${clip.video.channel || clip.video.title}`;

  return <article
    className="sur-player"
    onKeyDown={handleKeys}
    tabIndex={0}
    aria-label={label}
    lang={sourceLanguage ?? clip.source_language}
    data-status={status}
  >
    <span className="sur-player__sr-status" role="status" aria-live="polite">{statusMessage(status)}</span>
    <div className="sur-player__chrome">
      <div className="sur-player__video-stage" ref={hostRef} />
      {(status === "connecting" || status === "priming" || status === "starting") && <div className="sur-player__cover sur-player__cover--loading">
        <span className="sur-player__spinner" aria-hidden="true" />
        {status === "starting" ? "Loading excerpt…" : "Preparing excerpt…"}
      </div>}
      {status === "awaiting-interaction" && <div className="sur-player__cover sur-player__cover--awaiting">
        <button type="button" className="sur-player__stage-play" onClick={togglePlayback} aria-label="Load and play excerpt"><PlayIcon /></button>
      </div>}
      {status === "error" && <div className="sur-player__cover sur-player__cover--error" role="alert">
        <span className="sur-player__error-symbol" aria-hidden="true">!</span>
        <strong>Embedded playback unavailable</strong>
        <span>{playerError?.message}</span>
        {playerError?.recoverable && <button type="button" className="sur-player__retry" onClick={() => setLoadAttempt((value) => value + 1)}>Retry embedded player</button>}
        <a href={sourceUrl} target="_blank" rel="noreferrer">Open this moment at the source ↗</a>
      </div>}
      {interactive && <button
        type="button"
        className="sur-player__playback-shield"
        onClick={togglePlayback}
        aria-label={active ? "Pause video" : status === "ended" ? "Replay video excerpt" : "Play video"}
      />}
    </div>

    <div className={`sur-player__transport${showReplayControl ? "" : " sur-player__transport--no-replay"}`}>
      <button type="button" className="sur-player__transport-primary" onClick={togglePlayback} disabled={disabled}
        aria-label={active ? "Pause excerpt" : status === "ended" ? "Replay excerpt" : "Play excerpt"}>
        <PlayIcon pause={active} />
      </button>
      {showReplayControl && <button type="button" className="sur-player__transport-secondary" onClick={replay}
        disabled={navigationDisabled} aria-label="Replay from excerpt start">
        <ReplayIcon />
      </button>}
      <div className="sur-player__timeline">
        <input className="sur-player__range" type="range" min="0" max={duration} step="0.1"
          value={relativeCurrent} onChange={(event) => seek(Number(event.target.value))}
          disabled={navigationDisabled} aria-label="Excerpt position"
          style={{ "--sur-player-progress": progress } as CSSProperties} />
        <div className="sur-player__time-row">
          <span>{formatClock(relativeCurrent)} <small>/ {formatClock(duration)}</small></span>
          {status === "buffering" && <span className="sur-player__buffering"><i />Buffering</span>}
        </div>
      </div>
    </div>

    <div className="sur-player__copy">
      <p className="sur-player__source-text">
        <ProgressiveSourceText text={text} match={match} timing={timing} currentTime={current} />
      </p>
      {targetLanguage && (targetText || translationStatus !== "not_requested") && <div
        className="sur-player__translation"
        lang={targetLanguage}
        aria-live="polite"
        data-provenance={translationProvenance || undefined}
      >
        {targetText ? <p className="sur-player__target-text">
          {translationProvenance !== "authored_track" && alignmentGroups?.length
            ? <ProgressiveTargetText text={targetText} groups={alignmentGroups} timing={timing} currentTime={current} />
            : targetText}
        </p> : (translationStatus === "queued" || translationStatus === "running") ? <p className="sur-player__translation-status">
          Translating… {onTranslationCancel && <button type="button" onClick={onTranslationCancel}>Cancel</button>}
        </p> : <p className="sur-player__translation-status">
          {translationError || (translationStatus === "unavailable" ? "Translation is unavailable." : "Translation could not be loaded.")}
        </p>}
      </div>}
      <div className="sur-player__source-line">
        <span>{clip.video.channel}</span>
        {!blind && "rank" in clip && <span className="sur-player__evaluation-meta">Rank {clip.rank} · Score {clip.score.toFixed(3)}</span>}
        <a href={sourceUrl} target="_blank" rel="noreferrer">{formatClock(clip.clip_start)} at source ↗</a>
      </div>
    </div>
  </article>;
}

export default SpeechClipPlayer;
