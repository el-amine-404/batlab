import {
  Captions, Check, Gauge, Info, Loader2, Maximize, Minimize, Pause, PictureInPicture2, Play, RotateCcw, RotateCw, Volume1, Volume2, VolumeX,
} from "lucide-react";
import { type KeyboardEvent, type ReactNode, type Ref, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import type { FileDetails } from "@/lib/api";
import { local } from "@/lib/api";
import { clock, cn } from "@/lib/utils";

import { SeekBar } from "./SeekBar";
import { Stats } from "./Stats";

export type PlayerHandle = {
  /** Goes to time; plays from there when play. */
  seek: (time: number, play?: boolean) => void;
};

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const HIDE_AFTER = 2500;
// A position this close to either end isn't worth coming back to.
const RESUME_MARGIN = 10;

/** The browser's storage, when it lets us (a private window may not). */
const stored = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string | null) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch {
      // not kept: no harm
    }
  },
};

function ControlButton({ label, onClick, children, active = false, className }: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  active?: boolean;
  className?: string;
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
            className={cn("flex size-9 shrink-0 items-center justify-center rounded-md text-white/85 transition-colors hover:bg-white/10 hover:text-white [&_svg]:size-[18px]",
                          active && "text-sky-300", className)}>
      {children}
    </button>
  );
}

function Menu({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="absolute right-0 bottom-11 z-30 min-w-40 rounded-lg border border-white/15 bg-black/90 p-1 text-sm text-white shadow-xl backdrop-blur"
         role="menu" onClick={(event) => event.stopPropagation()}>
      <p className="px-2.5 pt-1 pb-1.5 text-[11px] font-medium tracking-wide text-white/50 uppercase">{title}</p>
      {children}
    </div>
  );
}

function MenuItem({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" role="menuitemradio" aria-checked={selected} onClick={onClick}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left hover:bg-white/10">
      <Check className={cn("size-3.5 shrink-0", !selected && "invisible")} /> {children}
    </button>
  );
}

/**
 * A video player for a downloaded file: the browser's own decoding (so it
 * costs what a <video> costs), with controls made for these files. The seek
 * bar shows the video's storyboard and chapters; its subtitle tracks (WebVTT
 * made from the file's) can be turned on; speed, picture-in-picture,
 * fullscreen; a panel with the file's details and how it plays (i). The
 * keyboard works as on YouTube: k or space, j and l, arrows, f, m, c, i,
 * , and . frame by frame, 0 to 9. Volume, and where you were in this video,
 * are remembered.
 */
export function Player({ src, details, poster, name, remember, compact = false, autoPlay = false, startAt, className, ref }: {
  src: string;
  details?: FileDetails;
  poster?: string;
  name?: string;
  /** Where to remember the position (one key per file); none: not remembered. */
  remember?: string;
  compact?: boolean;
  autoPlay?: boolean;
  startAt?: number;
  className?: string;
  ref?: Ref<PlayerHandle>;
}) {
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const played = useRef<HTMLDivElement>(null);
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [started, setStarted] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(details?.duration ?? 0);
  const [buffered, setBuffered] = useState<[number, number][]>([]);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [waiting, setWaiting] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [pip, setPip] = useState(false);
  const [shown, setShown] = useState(true);
  const [menu, setMenu] = useState<"speed" | "subtitles" | null>(null);
  const [stats, setStats] = useState(false);
  const [track, setTrack] = useState(-1);
  const [scrub, setScrub] = useState<number | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [resumed, setResumed] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const hideTimer = useRef<number | undefined>(undefined);
  const flashTimer = useRef<number | undefined>(undefined);

  const subtitles = (details?.subtitles ?? []).filter((s) => s.url);

  // ---- what the video does ------------------------------------------------------------

  const say = useCallback((text: string) => {
    setFlash(text);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 700);
  }, []);

  const wake = useCallback(() => {
    setShown(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (video.current && !video.current.paused) {
        setShown(false);
        setMenu(null);
      }
    }, HIDE_AFTER);
  }, []);

  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused || v.ended) void v.play().catch(() => undefined);
    else v.pause();
  }, []);

  const seek = useCallback((to: number, play = false) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.min(Math.max(0, to), v.duration || to);
    if (play) void v.play().catch(() => undefined);
  }, []);

  useImperativeHandle(ref, () => ({ seek }), [seek]);

  const skip = useCallback((seconds: number) => {
    const v = video.current;
    if (!v) return;
    seek(v.currentTime + seconds);
    say(`${seconds > 0 ? "+" : "−"}${Math.abs(seconds)} s`);
  }, [seek, say]);

  const toFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    else void box.current?.requestFullscreen().catch(() => undefined);
  }, []);

  const togglePip = useCallback(() => {
    const v = video.current;
    if (!v || !document.pictureInPictureEnabled) return;
    if (document.pictureInPictureElement) void document.exitPictureInPicture().catch(() => undefined);
    else void v.requestPictureInPicture().catch(() => undefined);
  }, []);

  const changeVolume = useCallback((to: number) => {
    const v = video.current;
    if (!v) return;
    v.volume = Math.min(1, Math.max(0, to));
    v.muted = v.volume === 0;
  }, []);

  const changeRate = useCallback((to: number) => {
    if (video.current) video.current.playbackRate = to;
    setMenu(null);
  }, []);

  const showTrack = useCallback((index: number) => {
    const v = video.current;
    if (!v) return;
    for (let n = 0; n < v.textTracks.length; n += 1) v.textTracks[n].mode = n === index ? "showing" : "disabled";
    setTrack(index);
    setMenu(null);
  }, []);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    setVideoElement(v);
    const saved = Number(stored.get("videodrop.volume"));
    if (stored.get("videodrop.volume") !== null && Number.isFinite(saved)) v.volume = Math.min(1, Math.max(0, saved));
    let lastSaved = 0;

    const onTime = () => {
      setTime(v.currentTime);
      if (remember && Math.abs(v.currentTime - lastSaved) >= 5) {
        lastSaved = v.currentTime;
        const keep = v.currentTime > RESUME_MARGIN && v.currentTime < (v.duration || Infinity) - RESUME_MARGIN;
        stored.set(remember, keep ? String(Math.round(v.currentTime)) : null);
      }
    };
    const onMeta = () => {
      setDuration(v.duration || 0);
      if (startAt !== undefined) {
        v.currentTime = startAt;
      } else if (remember) {
        const at = Number(stored.get(remember));
        if (at > RESUME_MARGIN && at < v.duration - RESUME_MARGIN) {
          v.currentTime = at;
          setResumed(at);
          window.setTimeout(() => setResumed(null), 5000);
        }
      }
    };
    const onProgress = () => {
      const ranges: [number, number][] = [];
      for (let n = 0; n < v.buffered.length; n += 1) ranges.push([v.buffered.start(n), v.buffered.end(n)]);
      setBuffered(ranges);
    };
    const onPlay = () => {
      setPlaying(true);
      setStarted(true);
      setFailed(false);
      wake();
    };
    const onPause = () => {
      setPlaying(false);
      setShown(true);
    };
    const onVolume = () => {
      setVolume(v.volume);
      setMuted(v.muted);
      stored.set("videodrop.volume", String(v.volume));
    };
    const handlers: [string, () => void][] = [
      ["timeupdate", onTime], ["loadedmetadata", onMeta], ["durationchange", () => setDuration(v.duration || 0)],
      ["progress", onProgress], ["play", onPlay], ["pause", onPause], ["ended", onPause], ["volumechange", onVolume],
      ["ratechange", () => setRate(v.playbackRate)], ["waiting", () => setWaiting(true)], ["seeking", () => setWaiting(true)],
      ["playing", () => setWaiting(false)], ["seeked", () => setWaiting(false)], ["canplay", () => setWaiting(false)],
      ["error", () => setFailed(true)], ["enterpictureinpicture", () => setPip(true)], ["leavepictureinpicture", () => setPip(false)],
    ];
    for (const [name, handler] of handlers) v.addEventListener(name, handler);
    onVolume();
    const onFullscreen = () => setFullscreen(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => {
      for (const [name, handler] of handlers) v.removeEventListener(name, handler);
      document.removeEventListener("fullscreenchange", onFullscreen);
    };
  }, [remember, startAt, wake]);

  // The played part of the bar follows the video every frame while it plays; React isn't asked.
  useEffect(() => {
    const v = video.current;
    if (!v || !played.current) return;
    let frame = 0;
    const draw = () => {
      const at = scrub ?? v.currentTime;
      if (played.current && v.duration) played.current.style.width = `${(at / v.duration) * 100}%`;
      if (playing) frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [playing, scrub, time, duration]);

  useEffect(() => () => {
    window.clearTimeout(hideTimer.current);
    window.clearTimeout(flashTimer.current);
  }, []);

  // ---- the keyboard -------------------------------------------------------------------

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const v = video.current;
    if (!v) return;
    const handled = () => {
      event.preventDefault();
      wake();
    };
    switch (event.key) {
      case " ": case "k": toggle(); handled(); break;
      case "j": skip(-10); handled(); break;
      case "l": skip(10); handled(); break;
      case "ArrowLeft": skip(-5); handled(); break;
      case "ArrowRight": skip(5); handled(); break;
      case "ArrowUp": changeVolume(v.volume + 0.05); say(`${Math.round(Math.min(1, v.volume + 0.05) * 100)}%`); handled(); break;
      case "ArrowDown": changeVolume(v.volume - 0.05); say(`${Math.round(Math.max(0, v.volume - 0.05) * 100)}%`); handled(); break;
      case "m": v.muted = !v.muted; say(v.muted ? "Muted" : "Sound on"); handled(); break;
      case "f": toFullscreen(); handled(); break;
      case "c":
        if (subtitles.length) {
          const next = track === -1 ? 0 : -1;
          showTrack(next);
          say(next === -1 ? "Subtitles off" : subtitles[0].title ?? subtitles[0].language ?? "Subtitles");
        }
        handled();
        break;
      case "i": setStats((open) => !open); handled(); break;
      case "Home": seek(0); handled(); break;
      case "End": seek(v.duration); handled(); break;
      case ",": case ".": {
        // One frame back or on, paused.
        v.pause();
        const fps = details?.video?.fps || 30;
        seek(v.currentTime + (event.key === "." ? 1 : -1) / fps);
        handled();
        break;
      }
      case "<": case ">": {
        const now = SPEEDS.indexOf(v.playbackRate);
        const next = SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, (now === -1 ? 3 : now) + (event.key === ">" ? 1 : -1)))];
        changeRate(next);
        say(`${next}×`);
        handled();
        break;
      }
      default:
        if (/^[0-9]$/.test(event.key) && v.duration) {
          seek((Number(event.key) / 10) * v.duration);
          handled();
        }
    }
  };

  // ---- drawing ------------------------------------------------------------------------

  const VolumeIcon = muted || volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;
  const chapter = details?.chapters.find((c) => time >= c.start && time < c.end)?.title;
  const controls = shown || !playing || menu !== null;

  return (
    <div ref={box} tabIndex={0} onKeyDown={onKey} onPointerMove={wake} onPointerLeave={() => playing && setShown(false)}
         className={cn("group/player relative isolate overflow-hidden bg-black outline-none select-none focus-visible:ring-2 focus-visible:ring-ring",
                       fullscreen ? "h-full w-full" : "aspect-video w-full", !controls && "cursor-none", className)}
         aria-label={name ? `Player: ${name}` : "Player"}>
      <video ref={video} src={src} poster={poster} preload="metadata" playsInline autoPlay={autoPlay}
             className="size-full object-contain" onClick={() => { toggle(); setMenu(null); }} onDoubleClick={toFullscreen}>
        {subtitles.map((s) => (
          <track key={s.index} kind="subtitles" src={local(s.url!)} srcLang={s.language && s.language !== "und" ? s.language : undefined}
                 label={s.title ?? s.language ?? `Track ${s.index + 1}`} />
        ))}
      </video>

      {/* Before the first play: one big button. */}
      {!started && !failed && (
        <button type="button" onClick={toggle} aria-label="Play"
                className="absolute inset-0 m-auto flex size-16 items-center justify-center rounded-full bg-black/55 text-white shadow-2xl ring-1 ring-white/20 backdrop-blur transition-transform hover:scale-105 sm:size-20">
          <Play className="ml-1 size-7 fill-current sm:size-9" />
        </button>
      )}
      {waiting && started && <Loader2 className="pointer-events-none absolute inset-0 m-auto size-10 animate-spin text-white/80" />}
      {flash && (
        <span className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rounded-lg bg-black/70 px-3 py-1.5 text-sm font-medium text-white">{flash}</span>
      )}
      {failed && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/80 p-4 text-center text-sm text-white">
          <p className="font-medium">This browser can't play the file.</p>
          <p className="text-white/60">Its codec ({details?.video?.codec ?? "unknown"}) may not be one it decodes. Download it and play it in VLC or mpv.</p>
        </div>
      )}
      {resumed !== null && (
        <div className="absolute top-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/80 py-1 pr-1 pl-3 text-xs text-white">
          Back where you were, at {clock(resumed)}
          <button type="button" className="rounded-full bg-white/15 px-2 py-0.5 hover:bg-white/25" onClick={() => { seek(0); setResumed(null); }}>Start over</button>
        </div>
      )}
      {stats && <Stats video={videoElement} details={details} name={name} onClose={() => setStats(false)} />}

      <div className={cn("absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-black/85 via-black/40 to-transparent px-2 pt-10 pb-1 transition-opacity duration-200 sm:px-3",
                         controls ? "opacity-100" : "pointer-events-none opacity-0")}>
        <SeekBar duration={duration} buffered={buffered} details={details} played={played} compact={compact}
                 onScrub={setScrub} onSeek={(to) => seek(to)} />
        <div className="flex items-center gap-0.5 text-white">
          <ControlButton label={playing ? "Pause (k)" : "Play (k)"} onClick={toggle}>
            {playing ? <Pause className="fill-current" /> : <Play className="fill-current" />}
          </ControlButton>
          {!compact && (
            <>
              <ControlButton label="Back 10 s (j)" onClick={() => skip(-10)}><RotateCcw /></ControlButton>
              <ControlButton label="On 10 s (l)" onClick={() => skip(10)}><RotateCw /></ControlButton>
            </>
          )}
          <div className="group/volume flex items-center">
            <ControlButton label={muted ? "Sound on (m)" : "Mute (m)"} onClick={() => { if (video.current) video.current.muted = !video.current.muted; }}>
              <VolumeIcon />
            </ControlButton>
            <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} aria-label="Volume"
                   onChange={(event) => changeVolume(Number(event.target.value))}
                   className="h-1 w-0 cursor-pointer accent-sky-400 opacity-0 transition-all group-hover/volume:w-20 group-hover/volume:opacity-100 focus-visible:w-20 focus-visible:opacity-100" />
          </div>
          <span className="ml-1.5 shrink-0 text-xs tabular-nums text-white/85">
            {clock(scrub ?? time)} <span className="text-white/50">/ {clock(duration)}</span>
          </span>
          {chapter && !compact && <span className="ml-2 min-w-0 truncate text-xs text-white/70">· {chapter}</span>}
          <span className="flex-1" />
          {subtitles.length > 0 && (
            <div className="relative">
              <ControlButton label="Subtitles (c)" onClick={() => setMenu(menu === "subtitles" ? null : "subtitles")} active={track !== -1}><Captions /></ControlButton>
              {menu === "subtitles" && (
                <Menu title="Subtitles">
                  <MenuItem selected={track === -1} onClick={() => showTrack(-1)}>Off</MenuItem>
                  {subtitles.map((s, n) => (
                    <MenuItem key={s.index} selected={track === n} onClick={() => showTrack(n)}>
                      {s.title ?? s.language ?? `Track ${s.index + 1}`}{s.title && s.language && s.language !== "und" ? ` (${s.language})` : ""}
                    </MenuItem>
                  ))}
                </Menu>
              )}
            </div>
          )}
          <div className="relative">
            <ControlButton label="Speed (< >)" onClick={() => setMenu(menu === "speed" ? null : "speed")} active={rate !== 1}>
              {rate !== 1 ? <span className="text-xs font-semibold tabular-nums">{rate}×</span> : <Gauge />}
            </ControlButton>
            {menu === "speed" && (
              <Menu title="Speed">
                {SPEEDS.map((speed) => (
                  <MenuItem key={speed} selected={rate === speed} onClick={() => changeRate(speed)}>{speed === 1 ? "Normal" : `${speed}×`}</MenuItem>
                ))}
              </Menu>
            )}
          </div>
          <ControlButton label="The file's details (i)" onClick={() => setStats(!stats)} active={stats}><Info /></ControlButton>
          {!compact && typeof document !== "undefined" && document.pictureInPictureEnabled && (
            <ControlButton label="Picture in picture" onClick={togglePip} active={pip}><PictureInPicture2 /></ControlButton>
          )}
          <ControlButton label={fullscreen ? "Leave full screen (f)" : "Full screen (f)"} onClick={toFullscreen}>
            {fullscreen ? <Minimize /> : <Maximize />}
          </ControlButton>
        </div>
      </div>
    </div>
  );
}
