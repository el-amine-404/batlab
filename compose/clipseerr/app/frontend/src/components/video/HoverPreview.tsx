import { Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Video } from "@/lib/api";

/** How long the pointer rests on a card, or a finger holds it, before its preview plays. */
const HOVER_MS = 700;
const HOLD_MS = 450;
const SOUND_KEY = "preview-sound";

// One preview at a time on the whole page: starting one stops the last.
let stopCurrent: (() => void) | null = null;
// The browser refused a preview's sound this visit: the next ones start muted, until the speaker is clicked.
let soundRefused = false;
// Videos with no preview (the server couldn't cut one): their cards stop offering it this visit.
const unplayable = new Set<string>();
// Clips ready on the server this visit: played at once, no asking first.
const ready = new Set<string>();
// Previews resting (the server's budget with YouTube spent) until then.
let restingUntil = 0;

/**
 * Has the server cut a video's clip, answering once it's ready: "ready",
 * "none" (the video has no preview) or "resting" (not now).
 */
async function clipFor(youtubeId: string): Promise<"ready" | "none" | "resting"> {
  if (ready.has(youtubeId)) return "ready";
  if (Date.now() < restingUntil) return "resting";
  try {
    const answer = await fetch(`/api/v1/previews/${youtubeId}/prepare`, { method: "POST" });
    if (answer.ok) {
      ready.add(youtubeId);
      return "ready";
    }
    if (answer.status === 404) return "none";
    if (answer.status === 429) restingUntil = Date.now() + 60_000;
  } catch {
    // offline, or the server restarting: nothing to play now
  }
  return "resting";
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
 * The preview itself: a plain video, the clip clipseerr cuts
 * (/api/v1/previews/{id}: 15 s from a third in) or Apple's 30 s preview, so
 * nothing but the picture shows. Played round and round, seen once it plays,
 * its sound only if the browser allows it.
 */
function PreviewVideo({ src, sound, onShown, onProgress, onSoundRefused, onFailed }: {
  src: string;
  sound: boolean;
  onShown: () => void;
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
      loop
      playsInline
      preload="auto"
      disablePictureInPicture
      className="pointer-events-none absolute inset-0 size-full bg-black object-cover transition-opacity duration-200"
      style={{ opacity: shown ? 1 : 0 }}
      onPlaying={() => {
        setShown(true);
        onShown();
      }}
      onTimeUpdate={(event) => {
        const el = event.currentTarget;
        if (el.duration) onProgress(el.currentTime / el.duration);
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
  // Its clip on the server, ready to play: the first hover of a video waits about 2.5 s for its cut.
  const [clip, setClip] = useState(false);
  // Playing, not waiting any more.
  const [shown, setShown] = useState(false);
  // Which start the clip's answer belongs to: one that comes after the pointer left is dropped.
  const attempt = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const held = useRef(false);
  const playable = video.youtube_id ? !unplayable.has(video.youtube_id) : !!video.preview_url;

  const stop = () => {
    window.clearTimeout(timer.current);
    attempt.current += 1;
    setPlaying(false);
    setClip(false);
    setShown(false);
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
      const id = video.youtube_id;
      if (!id) {
        setClip(true);
        return;
      }
      const mine = ++attempt.current;
      void clipFor(id).then((answer) => {
        if (attempt.current !== mine) return;
        if (answer === "ready") return setClip(true);
        if (answer === "none") unplayable.add(id);
        stop();
      });
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

  const preview = playing ? (
    <span className="absolute inset-0 overflow-hidden">
      {clip && <PreviewVideo
        src={video.youtube_id ? `/api/v1/previews/${video.youtube_id}` : video.preview_url!}
        sound={sound}
        onShown={() => setShown(true)}
        onProgress={setProgress}
        onSoundRefused={() => { soundRefused = true; setSound(false); }}
        onFailed={() => {
          if (video.youtube_id) unplayable.add(video.youtube_id);
          stop();
        }}
      />}
      <button type="button" onClick={toggleSound} onPointerDown={(e) => e.stopPropagation()}
              aria-label={sound ? "Mute the preview" : "Play the preview's sound"}
              className="absolute bottom-2 left-2 z-10 flex size-7 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur-md hover:bg-black/90">
        {sound ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />}
      </button>
      <span className="absolute inset-x-0 bottom-0 z-10 h-1 overflow-hidden bg-white/15" aria-hidden>
        {shown ? (
          <span className="block h-full bg-rose-500 transition-[width] duration-300 ease-linear" style={{ width: `${progress * 100}%` }} />
        ) : (
          // Its clip on the way: a light running along the bar.
          <span className="preview-wait block h-full w-1/3 bg-gradient-to-r from-transparent via-rose-400 to-transparent" />
        )}
      </span>
    </span>
  ) : null;
  return { handlers, preview, playing, shown };
}
