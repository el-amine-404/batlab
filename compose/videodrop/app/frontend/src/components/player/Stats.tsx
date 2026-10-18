import { X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import type { FileDetails } from "@/lib/api";
import { clock, formatBitrate, formatBytes } from "@/lib/utils";

import { audioName, videoName } from "./names";

const READY = ["nothing", "metadata", "current frame", "future frames", "enough to play"];
const NETWORK = ["empty", "idle", "loading", "no source"];

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-white/55">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

type Live = {
  decoded: string;
  shown: string;
  frames: string;
  ahead: number;
  loaded: number;
  rate: number;
  volume: string;
  state: string;
};

function measure(video: HTMLVideoElement): Live {
  const quality = video.getVideoPlaybackQuality?.();
  let ahead = 0;
  let loaded = 0;
  for (let n = 0; n < video.buffered.length; n += 1) {
    const start = video.buffered.start(n);
    const end = video.buffered.end(n);
    loaded += end - start;
    if (start <= video.currentTime && video.currentTime <= end) ahead = end - video.currentTime;
  }
  const ratio = window.devicePixelRatio || 1;
  return {
    decoded: video.videoWidth ? `${video.videoWidth}×${video.videoHeight}` : "–",
    shown: `${video.clientWidth}×${video.clientHeight}${ratio !== 1 ? ` ×${ratio}` : ""}`,
    frames: quality ? `${quality.droppedVideoFrames.toLocaleString()} dropped of ${quality.totalVideoFrames.toLocaleString()}` : "–",
    ahead,
    loaded,
    rate: video.playbackRate,
    volume: video.muted ? "muted" : `${Math.round(video.volume * 100)}%`,
    state: `${READY[video.readyState] ?? video.readyState} · network ${NETWORK[video.networkState] ?? video.networkState}`,
  };
}

/** For a closer look (key i): the file as ffprobe read it, and how the browser is playing it, live. */
export function Stats({ video, details, name, onClose }: {
  video: HTMLVideoElement | null;
  details?: FileDetails;
  name?: string;
  onClose: () => void;
}) {
  const [live, setLive] = useState<Live | null>(null);
  useEffect(() => {
    if (!video) return;
    const read = () => setLive(measure(video));
    read();
    const timer = window.setInterval(read, 500);
    return () => window.clearInterval(timer);
  }, [video]);
  const v = details?.video;
  return (
    <div className="absolute top-2 left-2 z-20 max-h-[calc(100%-5rem)] w-[min(26rem,calc(100%-1rem))] overflow-y-auto rounded-lg border border-white/15 bg-black/80 p-3 font-mono text-[11px] leading-relaxed text-white shadow-xl backdrop-blur-sm"
         onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
      <button type="button" onClick={onClose} aria-label="Close the details" className="absolute top-1.5 right-1.5 rounded p-1 text-white/60 hover:bg-white/10 hover:text-white">
        <X className="size-3.5" />
      </button>
      <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3">
        {name && <Line label="File">{name}</Line>}
        {details && (
          <>
            <Line label="Container">{details.container ?? "–"} · {formatBytes(details.size)} ({details.size.toLocaleString()} B)</Line>
            <Line label="Length">{details.duration ? clock(details.duration) : "–"}{details.kbps ? ` · ${formatBitrate(details.kbps)} overall` : ""}</Line>
            {v && <Line label="Video">{videoName(v.codec)}{v.profile ? ` (${v.profile})` : ""} · {v.width}×{v.height}{v.fps ? ` @ ${v.fps} fps` : ""}{v.kbps ? ` · ${formatBitrate(v.kbps)}` : ""}</Line>}
            {v && <Line label="Colour">{v.pixel_format ?? "–"} · {v.bit_depth}-bit · {v.dynamic_range}</Line>}
            {details.audio.map((a, n) => (
              <Line key={n} label={n ? `Sound ${n + 1}` : "Sound"}>
                {audioName(a.codec)}{a.profile ? ` (${a.profile})` : ""} · {a.layout ?? `${a.channels} ch`}{a.sample_rate ? ` · ${a.sample_rate / 1000} kHz` : ""}{a.kbps ? ` · ${formatBitrate(a.kbps)}` : ""}{a.language && a.language !== "und" ? ` · ${a.language}` : ""}
              </Line>
            ))}
            <Line label="Subtitles">{details.subtitles.length ? details.subtitles.map((s) => s.title ?? s.language ?? s.codec).join(", ") : "none"}</Line>
            <Line label="Chapters">{details.chapters.length || "none"}</Line>
          </>
        )}
        {live && (
          <>
            <dt className="col-span-2 mt-2 mb-0.5 text-white/40">— playing —</dt>
            <Line label="Decoded">{live.decoded}</Line>
            <Line label="Shown at">{live.shown}</Line>
            <Line label="Frames">{live.frames}</Line>
            <Line label="Buffer">{live.ahead.toFixed(1)} s ahead · {clock(live.loaded)} loaded</Line>
            <Line label="Speed">{live.rate}× · volume {live.volume}</Line>
            <Line label="State">{live.state}</Line>
          </>
        )}
      </dl>
    </div>
  );
}
