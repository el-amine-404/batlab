import { useQuery } from "@tanstack/react-query";
import { Images } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, type VideoDetail } from "@/lib/api";
import { playback } from "@/lib/playback";

function clock(seconds: number) {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * The video at a glance: a few frames along it (YouTube's storyboard, one
 * every ~15 s), each a click away from playing from there. Asked for only
 * when it scrolls into view: a page opened and left costs nothing.
 */
export function Moments({ video }: { video: VideoDetail }) {
  const box = useRef<HTMLElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!box.current || seen) return;
    const watch = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setSeen(true), { rootMargin: "200px" });
    watch.observe(box.current);
    return () => watch.disconnect();
  }, [seen]);
  const { data, isPending, error } = useQuery({
    queryKey: ["frames", video.id], queryFn: ({ signal }) => api.frames(video.id, signal),
    enabled: seen && !!video.youtube_id, staleTime: Infinity, retry: false,
  });

  if (!video.youtube_id || error || (data && !data.frames.length)) return null;
  const tile = data?.tile;
  return (
    <section ref={box} aria-label="Moments of the video">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold [&_svg]:size-4 [&_svg]:text-rose-300"><Images /> Moments</h2>
      <ol className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {!data || !tile || isPending
          ? Array.from({ length: 6 }, (_, i) => <li key={i} className="skeleton aspect-video rounded-lg" aria-hidden />)
          : data.frames.map((frame) => (
            <li key={frame.t}>
              <button type="button" onClick={() => playback.jump(frame.t)} title={`Play from ${clock(frame.t)}`}
                      className="group relative block aspect-video w-full overflow-hidden rounded-lg bg-black ring-1 ring-white/10 transition hover:ring-rose-300/60">
                <span aria-hidden className="absolute inset-0 transition-transform duration-300 group-hover:scale-105"
                      style={{
                        backgroundImage: `url(${api.frameSheet(video.youtube_id!, frame.sheet)})`,
                        backgroundSize: `${tile.columns * 100}% ${tile.rows * 100}%`,
                        backgroundPosition: `${tile.columns > 1 ? (frame.col / (tile.columns - 1)) * 100 : 0}% ${tile.rows > 1 ? (frame.row / (tile.rows - 1)) * 100 : 0}%`,
                      }} />
                <span className="absolute right-1 bottom-1 rounded bg-black/70 px-1 text-[10px] font-medium text-white tabular-nums">{clock(frame.t)}</span>
              </button>
            </li>
          ))}
      </ol>
    </section>
  );
}
