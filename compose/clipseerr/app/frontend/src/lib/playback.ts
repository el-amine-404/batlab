/**
 * Where the player is, for whoever follows it (the lyrics): what plays (the
 * video or the song), how far in, and whether it is playing. The YouTube
 * player tells it through its iframe API (postMessage); seek() asks it back.
 */
import { useSyncExternalStore } from "react";

export type Playback = { mode: "video" | "audio" | "preview" | null; time: number; playing: boolean; videoId: string | null };

let state: Playback = { mode: null, time: 0, playing: false, videoId: null };
let frame: HTMLIFrameElement | null = null;
const listeners = new Set<() => void>();

export const playback = {
  get: () => state,
  set(next: Partial<Playback>) {
    state = { ...state, ...next };
    listeners.forEach((listener) => listener());
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** The YouTube iframe playing now, or null when it is gone. */
  attach(iframe: HTMLIFrameElement | null) {
    frame = iframe;
  },
  /** Play the video from there: the player starts there, or jumps there when it plays already. */
  jump(seconds: number) {
    window.dispatchEvent(new CustomEvent("clipseerr:jump", { detail: seconds }));
  },
  seek(seconds: number) {
    frame?.contentWindow?.postMessage(JSON.stringify({ event: "command", func: "seekTo", args: [seconds, true] }), "*");
    frame?.contentWindow?.postMessage(JSON.stringify({ event: "command", func: "playVideo", args: [] }), "*");
  },
};

export function usePlayback(): Playback {
  return useSyncExternalStore(playback.subscribe, playback.get);
}

/** Asks a YouTube iframe to report its time and state; its messages land in playback. */
export function listen(iframe: HTMLIFrameElement, mode: Playback["mode"], videoId: string) {
  playback.attach(iframe);
  playback.set({ mode, videoId, time: 0, playing: false });
  // The player may still be starting: asked again a few times, until it answers.
  let tries = 0;
  const ask = () => {
    if (frame !== iframe || heard || tries++ > 20) return;
    iframe.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: videoId, channel: "widget" }), "*");
    window.setTimeout(ask, 500);
  };
  heard = false;
  ask();
}

let heard = false;

if (typeof window !== "undefined") {
  window.addEventListener("message", (event) => {
    if (!frame || event.source !== frame.contentWindow || typeof event.data !== "string") return;
    let data: { event?: string; info?: { currentTime?: number; playerState?: number } };
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    if (data.event !== "infoDelivery" && data.event !== "onStateChange" && data.event !== "initialDelivery") return;
    heard = true;
    const info = data.info ?? {};
    const next: Partial<Playback> = {};
    if (typeof info.currentTime === "number") next.time = info.currentTime;
    // 1 playing, 2 paused, 0 ended, 3 buffering.
    if (typeof info.playerState === "number") next.playing = info.playerState === 1 || info.playerState === 3;
    if (Object.keys(next).length) playback.set(next);
  });
}
