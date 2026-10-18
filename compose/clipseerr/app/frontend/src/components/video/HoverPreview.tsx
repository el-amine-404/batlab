import { Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Video } from "@/lib/api";

/** How long the pointer rests on a card, or a finger holds it, before its preview plays. */
const HOVER_MS = 700;
const HOLD_MS = 450;
/** Seconds of the video a preview plays, from about a third in (past the intro), then again. */
const LENGTH = 15;
const SOUND_KEY = "preview-sound";

// One preview at a time on the whole page: starting one stops the last.
let stopCurrent: (() => void) | null = null;

function soundWanted(): boolean {
  try {
    return localStorage.getItem(SOUND_KEY) !== "off";
  } catch {
    return true;
  }
}

/**
 * Browsers let a page play sound only once it has been clicked or tapped; a
 * preview before that plays muted, and its speaker says so.
 */
function soundAllowed(): boolean {
  const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return activation ? activation.hasBeenActive : true;
}

/** YouTube's player in a card: controlled through its iframe API, its own overlays kept out of sight. */
function YouTubePreview({ id, from, sound, onProgress }: { id: string; from: number; sound: boolean; onProgress: (p: number) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [shown, setShown] = useState(false);
  const send = (func: string, args: unknown[] = []) =>
    frame.current?.contentWindow?.postMessage(JSON.stringify({ event: "command", func, args }), "*");

  useEffect(() => {
    let heard = false;
    let tries = 0;
    const ask = () => {
      if (heard || tries++ > 20) return;
      frame.current?.contentWindow?.postMessage(JSON.stringify({ event: "listening", id, channel: "widget" }), "*");
      window.setTimeout(ask, 400);
    };
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || typeof event.data !== "string") return;
      let data: { event?: string; info?: { currentTime?: number; playerState?: number } };
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      heard = true;
      const info = data.info ?? {};
      // Shown once it plays: the spinner and the big play button it starts with stay hidden.
      if (info.playerState === 1) setShown(true);
      if (typeof info.currentTime === "number") {
        const into = info.currentTime - from;
        onProgress(Math.min(1, Math.max(0, into / LENGTH)));
        // Round again from the start, rather than YouTube's end screen.
        if (into >= LENGTH) send("seekTo", [from, true]);
      }
    };
    window.addEventListener("message", onMessage);
    ask();
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, from]);

  useEffect(() => {
    if (!shown) return;
    if (sound) {
      send("unMute");
      send("setVolume", [70]);
    } else {
      send("mute");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sound, shown]);

  return (
    <iframe
      ref={frame}
      title=""
      aria-hidden
      tabIndex={-1}
      // A little larger than the card: YouTube's title bar and watermark fall outside it.
      className="pointer-events-none absolute inset-0 size-full scale-[1.35] transition-opacity duration-300"
      style={{ opacity: shown ? 1 : 0 }}
      allow="autoplay; encrypted-media"
      src={`https://www.youtube-nocookie.com/embed/${id}?${new URLSearchParams({
        autoplay: "1", mute: "1", controls: "0", disablekb: "1", fs: "0", iv_load_policy: "3", modestbranding: "1",
        playsinline: "1", rel: "0", start: String(from), enablejsapi: "1", origin: window.location.origin,
      })}`}
    />
  );
}

/**
 * A short preview of a card's video while the pointer rests on it (desktop)
 * or a finger holds it (phone), with a bar for how far it is and its sound
 * once the browser allows it. Nothing loads until then, and it is gone when
 * the pointer leaves: a page of cards costs nothing more.
 * Returns the handlers for the card, and the preview to draw over its picture.
 */
export function useHoverPreview(video: Video) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [sound, setSound] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const held = useRef(false);
  const playable = !!video.youtube_id || !!video.preview_url;

  const stop = () => {
    window.clearTimeout(timer.current);
    setPlaying(false);
    setProgress(0);
  };
  const start = (delay: number, onStart?: () => void) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      stopCurrent?.();
      stopCurrent = stop;
      onStart?.();
      setSound(soundWanted() && soundAllowed());
      setPlaying(true);
    }, delay);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const toggleSound = (event: React.MouseEvent | React.PointerEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const next = !sound;
    setSound(next);
    try {
      localStorage.setItem(SOUND_KEY, next ? "on" : "off");
    } catch {
      // private window: the choice lasts this visit
    }
  };

  const handlers = playable ? {
    onPointerEnter: (event: React.PointerEvent) => event.pointerType === "mouse" && start(HOVER_MS),
    onPointerLeave: (event: React.PointerEvent) => event.pointerType === "mouse" && stop(),
    onTouchStart: () => {
      held.current = false;
      start(HOLD_MS, () => { held.current = true; });
    },
    onTouchMove: () => window.clearTimeout(timer.current),
    onTouchEnd: () => {
      window.clearTimeout(timer.current);
      if (held.current) stop();
    },
    // A hold that played the preview isn't a tap: don't open the video, nor the browser's menu.
    onClickCapture: (event: React.MouseEvent) => {
      if (held.current) {
        event.preventDefault();
        held.current = false;
      }
    },
    onContextMenu: (event: React.MouseEvent) => held.current && event.preventDefault(),
  } : {};

  const from = Math.round((video.duration ?? 90) / 3);
  const preview = playing ? (
    <span className="absolute inset-0 overflow-hidden bg-black">
      {video.youtube_id ? (
        <YouTubePreview id={video.youtube_id} from={from} sound={sound} onProgress={setProgress} />
      ) : (
        <video className="pointer-events-none absolute inset-0 size-full object-cover" src={video.preview_url!} muted={!sound} autoPlay loop
               playsInline onTimeUpdate={(e) => setProgress(e.currentTarget.currentTime / (e.currentTarget.duration || 30))} />
      )}
      <button type="button" onClick={toggleSound} onPointerDown={(e) => e.stopPropagation()}
              aria-label={sound ? "Mute the preview" : "Play the preview's sound"}
              className="absolute bottom-2 left-2 z-10 flex size-7 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur-md hover:bg-black/90">
        {sound ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />}
      </button>
      <span className="absolute inset-x-0 bottom-0 z-10 h-1 bg-white/15" aria-hidden>
        <span className="block h-full bg-rose-500 transition-[width] duration-300 ease-linear" style={{ width: `${progress * 100}%` }} />
      </span>
    </span>
  ) : null;
  return { handlers, preview, playing };
}
