import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Images, Play } from "lucide-react";
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

  // Which frames show, for the counter: the strip says as it scrolls.
  const strip = useRef<HTMLOListElement>(null);
  const [shown, setShown] = useState<[number, number]>([0, 0]);
  const measure = () => {
    const el = strip.current;
    if (!el || !el.children.length) return;
    const width = (el.children[0] as HTMLElement).offsetWidth + 8;
    const first = Math.round(el.scrollLeft / width);
    setShown([first, Math.min(el.children.length, first + Math.max(1, Math.floor((el.clientWidth + 8) / width)))]);
  };
  useEffect(measure, [data]);
  const scroll = (by: -1 | 1) => strip.current?.scrollBy({ left: by * strip.current.clientWidth * 0.9, behavior: "smooth" });

  if (!video.youtube_id || error || (data && !data.frames.length)) return null;
  const tile = data?.tile;
  const total = data?.frames.length ?? 0;
  return (
    <section ref={box} aria-label="Moments of the video">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold [&_svg]:size-4 [&_svg]:text-rose-300">
          <Images /> Moments
          {total > 0 && <span className="text-xs font-normal text-muted-foreground tabular-nums">{shown[0] + 1}–{shown[1]} of {total}</span>}
        </h2>
        <div className="flex gap-1">
          <button type="button" onClick={() => scroll(-1)} disabled={shown[0] === 0} aria-label="Earlier moments"
                  className="flex size-7 items-center justify-center rounded-full border border-border text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30">
            <ChevronLeft className="size-4" />
          </button>
          <button type="button" onClick={() => scroll(1)} disabled={shown[1] >= total} aria-label="Later moments"
                  className="flex size-7 items-center justify-center rounded-full border border-border text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-30">
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>
      <ol ref={strip} onScroll={measure}
          className="flex snap-x snap-mandatory gap-2 overflow-x-auto scroll-smooth pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {!data || !tile || isPending
          ? Array.from({ length: 6 }, (_, i) => <li key={i} className="skeleton aspect-video w-40 shrink-0 rounded-lg sm:w-48" aria-hidden />)
          : data.frames.map((frame, n) => (
            <li key={frame.t} className="w-40 shrink-0 snap-start sm:w-48">
              <button type="button" onClick={() => playback.jump(frame.t)} title={`Play from ${clock(frame.t)}`}
                      aria-label={`Moment ${n + 1} of ${total}: play from ${clock(frame.t)}`}
                      className="group relative block aspect-video w-full overflow-hidden rounded-lg bg-black ring-1 ring-white/10 transition hover:ring-rose-300/60">
                <span aria-hidden className="absolute inset-0 transition-transform duration-300 group-hover:scale-105"
                      style={{
                        backgroundImage: `url(${api.frameSheet(video.youtube_id!, frame.sheet)})`,
                        backgroundSize: `${tile.columns * 100}% ${tile.rows * 100}%`,
                        backgroundPosition: `${tile.columns > 1 ? (frame.col / (tile.columns - 1)) * 100 : 0}% ${tile.rows > 1 ? (frame.row / (tile.rows - 1)) * 100 : 0}%`,
                      }} />
                <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
                  <Play className="size-6 fill-white text-white drop-shadow" />
                </span>
                <span className="absolute right-1 bottom-1 rounded bg-black/70 px-1 text-[10px] font-medium text-white tabular-nums">{clock(frame.t)}</span>
              </button>
            </li>
          ))}
      </ol>
    </section>
  );
}
