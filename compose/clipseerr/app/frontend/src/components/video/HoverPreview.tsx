import { useEffect, useRef, useState } from "react";

import type { Video } from "@/lib/api";

/** How long the pointer rests on a card, or a finger holds it, before its preview plays. */
const HOVER_MS = 700;
const HOLD_MS = 450;
/** Seconds of the video a preview plays, from about a third in (past the intro). */
const LENGTH = 15;

// One preview at a time on the whole page: starting one stops the last.
let stopCurrent: (() => void) | null = null;

/**
 * A short, muted preview of a card's video while the pointer rests on it
 * (desktop) or a finger holds it (phone). Nothing loads until then, and it is
 * gone when the pointer leaves: a page of cards costs nothing more.
 * Returns the handlers for the card, and the preview to draw over its picture.
 */
export function useHoverPreview(video: Video) {
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const held = useRef(false);
  const playable = !!video.youtube_id || !!video.preview_url;

  const stop = () => {
    window.clearTimeout(timer.current);
    setPlaying(false);
  };
  const start = (delay: number, onStart?: () => void) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      stopCurrent?.();
      stopCurrent = stop;
      onStart?.();
      setPlaying(true);
    }, delay);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

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
    video.youtube_id ? (
      <iframe
        title=""
        aria-hidden
        tabIndex={-1}
        className="pointer-events-none absolute inset-0 size-full"
        allow="autoplay; encrypted-media"
        src={`https://www.youtube-nocookie.com/embed/${video.youtube_id}?${new URLSearchParams({
          autoplay: "1", mute: "1", controls: "0", disablekb: "1", fs: "0", iv_load_policy: "3", modestbranding: "1",
          playsinline: "1", rel: "0", start: String(from), end: String(from + LENGTH), loop: "1", playlist: video.youtube_id,
        })}`}
      />
    ) : (
      <video className="pointer-events-none absolute inset-0 size-full object-cover" src={video.preview_url!} muted autoPlay loop playsInline />
    )
  ) : null;
  return { handlers, preview, playing };
}
