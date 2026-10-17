import { Check, Clock3, Film, Music2, Play } from "lucide-react";
import { memo, useRef } from "react";
import { Link } from "react-router";

import type { Video } from "@/lib/api";
import { prefetchVideo } from "@/lib/queries";
import { clock, cn, day } from "@/lib/utils";

import { Thumb } from "./Thumb";
import type { Requested } from "./useRequested";

function RequestedMark({ requested }: { requested?: Requested }) {
  if (!requested) return null;
  const marks = (["audio", "video"] as const).filter((kind) => requested[kind] && requested[kind] !== "failed");
  if (!marks.length) return null;
  const done = marks.every((kind) => requested[kind] === "completed");
  return (
    <span
      className={cn(
        "absolute top-2 right-2 flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium backdrop-blur-md",
        done ? "bg-success/25 text-emerald-100" : "bg-black/60 text-rose-100",
      )}
      title={done ? "In your library" : "Requested"}
    >
      {done ? <Check className="size-3" /> : <Clock3 className="size-3" />}
      {marks.map((kind) => (kind === "audio" ? <Music2 key={kind} className="size-3" /> : <Film key={kind} className="size-3" />))}
    </span>
  );
}

/** One video in a list; the details load while the pointer is on it, so its page opens at once. */
export const VideoCard = memo(function VideoCard({ video, requested, className, eager }: {
  video: Video;
  requested?: Requested;
  className?: string;
  eager?: boolean;
}) {
  const timer = useRef<number | undefined>(undefined);
  const warm = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => prefetchVideo(video.id), 120);
  };
  const cool = () => window.clearTimeout(timer.current);
  const meta = [video.views && `${video.views} views`, video.released && day(video.released)].filter(Boolean).join(" · ");

  return (
    <Link
      to={`/watch/${video.id}`}
      onPointerEnter={warm}
      onPointerLeave={cool}
      onFocus={warm}
      onTouchStart={() => prefetchVideo(video.id)}
      className={cn("group flex min-w-0 flex-col gap-2.5 rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring", className)}
    >
      <div className="relative overflow-hidden rounded-xl ring-1 ring-white/5">
        <Thumb src={video.thumbnail} youtubeId={video.youtube_id} alt="" eager={eager}
               className="transition-transform duration-300 group-hover:scale-[1.03]" />
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
        <span className="pointer-events-none absolute top-1/2 left-1/2 flex size-11 -translate-x-1/2 -translate-y-1/2 scale-90 items-center justify-center rounded-full bg-white/90 text-black opacity-0 shadow-xl transition-all group-hover:scale-100 group-hover:opacity-100">
          <Play className="ml-0.5 size-5 fill-current" />
        </span>
        <div className="absolute top-2 left-2 flex gap-1">
          {video.source === "apple" && (
            <span className="rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white/90 backdrop-blur-md">
              APPLE
            </span>
          )}
          {video.explicit && (
            <span className="rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white/90 backdrop-blur-md" title="Explicit">
              E
            </span>
          )}
          {!video.official && (
            <span className="rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/80 backdrop-blur-md">
              Fan upload
            </span>
          )}
        </div>
        <RequestedMark requested={requested} />
        {!!video.duration && (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
            {clock(video.duration)}
          </span>
        )}
      </div>
      <div className="min-w-0 px-0.5">
        <p className="line-clamp-2 text-sm leading-snug font-medium text-foreground/95 group-hover:text-foreground">{video.title}</p>
        <p className="mt-0.5 truncate text-[13px] text-muted-foreground">{video.artist}</p>
        {meta && <p className="truncate text-xs text-muted-foreground/70">{meta}</p>}
      </div>
    </Link>
  );
});

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2.5", className)} aria-hidden>
      <div className="skeleton aspect-video rounded-xl" />
      <div className="skeleton h-3.5 w-4/5 rounded" />
      <div className="skeleton h-3 w-1/2 rounded" />
    </div>
  );
}
