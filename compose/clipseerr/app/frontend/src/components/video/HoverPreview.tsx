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
// The browser refused a preview's sound this visit: the next ones start muted, until the speaker is clicked.
let soundRefused = false;
const prepared = new Set<string>();

/** Has the server find a video's preview file now, once a visit: it then starts sooner. */
export function preparePreview(youtubeId: string) {
  if (prepared.has(youtubeId)) return;
  prepared.add(youtubeId);
  void fetch(`/api/v1/previews/${youtubeId}/prepare`, { method: "POST" }).catch(() => undefined);
}

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

/**
 * The preview itself: a plain video, the 360p file with sound that clipseerr
 * streams (/api/v1/previews/{id}) or Apple's preview, so nothing but the
 * picture shows. Seen once it plays, round again at the end of its stretch,
 * and its sound only if the browser allows it.
 */
function PreviewVideo({ src, from, length, sound, onProgress, onSoundRefused, onFailed }: {
  src: string;
  from: number;
  length: number;
  sound: boolean;
  onProgress: (p: number) => void;
  onSoundRefused: () => void;
  onFailed: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [shown, setShown] = useState(false);

  // The sound, asked for when wanted: a browser that refuses it rejects play(); then muted, and on.
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    el.muted = !sound;
    if (!sound) return;
    el.play().catch(() => {
      el.muted = true;
      void el.play().catch(() => undefined);
      onSoundRefused();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sound]);

  return (
    <video
      ref={video}
      src={src}
      muted
      autoPlay
      playsInline
      preload="auto"
      disablePictureInPicture
      className="pointer-events-none absolute inset-0 size-full bg-black object-cover transition-opacity duration-200"
      style={{ opacity: shown ? 1 : 0 }}
      onLoadedMetadata={(event) => {
        const el = event.currentTarget;
        if (from && from < el.duration) el.currentTime = from;
      }}
      onPlaying={() => setShown(true)}
      onTimeUpdate={(event) => {
        const el = event.currentTarget;
        const into = el.currentTime - from;
        onProgress(Math.min(1, Math.max(0, into / length)));
        if (into >= length) el.currentTime = from;
      }}
      onError={onFailed}
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
      setSound(soundWanted() && soundAllowed() && !soundRefused);
      setPlaying(true);
    }, delay);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const toggleSound = (event: React.MouseEvent | React.PointerEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const next = !sound;
    // A click is what the browser waits for: worth trying the sound again.
    soundRefused = false;
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

  // YouTube's: a stretch from a third in, past the intro. Apple's 30 s preview: all of it.
  const from = video.youtube_id ? Math.round((video.duration ?? 90) / 3) : 0;
  const preview = playing ? (
    <span className="absolute inset-0 overflow-hidden">
      <PreviewVideo
        src={video.youtube_id ? `/api/v1/previews/${video.youtube_id}` : video.preview_url!}
        from={from}
        length={video.youtube_id ? LENGTH : 30}
        sound={sound}
        onProgress={setProgress}
        onSoundRefused={() => { soundRefused = true; setSound(false); }}
        onFailed={stop}
      />
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
