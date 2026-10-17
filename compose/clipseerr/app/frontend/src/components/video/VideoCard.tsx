import { Check, Clock3, Film, Music2, Play } from "lucide-react";
import { memo, useRef } from "react";
import { Link } from "react-router";

import type { Video } from "@/lib/api";
import { Marquee } from "@/components/ui/marquee";
import { prefetchVideo, useFacts } from "@/lib/queries";
import { clock, cn, dated, upcoming } from "@/lib/utils";

import { useHoverPreview } from "./HoverPreview";
import { TagBadge } from "./TagBadge";
import { Thumb } from "./Thumb";
import type { Requested } from "./useRequested";

/**
 * What there is of a video already: a green check when its song or its file
 * is in a library (or a request for it finished), "Requested" while one is
 * under way. The icons say which: the song, the video.
 */
function StatusMark({ video, requested }: { video: Video; requested?: Requested }) {
  const kinds = ["audio", "video"] as const;
  const owned = kinds.filter((kind) => video.library?.includes(kind) || requested?.[kind] === "completed");
  const asked = kinds.filter((kind) => !owned.includes(kind) && requested?.[kind] && !["failed", "completed"].includes(requested[kind]!));
  if (!owned.length && !asked.length) return null;
  const icon = (kind: "audio" | "video") => (kind === "audio" ? <Music2 key={kind} className="size-3" /> : <Film key={kind} className="size-3" />);
  return (
    <span className="absolute top-2 right-2 flex flex-col items-end gap-1">
      {owned.length > 0 && (
        <span className="flex items-center gap-1 rounded-full bg-emerald-500 px-2 py-0.5 text-[11px] font-semibold text-white shadow-lg shadow-black/30"
              title={`In your library: ${owned.map((k) => (k === "audio" ? "the song" : "the video")).join(" and ")}`}>
          <Check className="size-3.5" strokeWidth={3} /> {owned.map(icon)}
        </span>
      )}
      {asked.length > 0 && (
        <span className="flex items-center gap-1 rounded-full bg-rose-500/90 px-2 py-0.5 text-[11px] font-semibold text-white shadow-lg shadow-black/30 backdrop-blur-md"
              title={`Requested: ${asked.map((k) => (k === "audio" ? "the song" : "the video")).join(" and ")}`}>
          <Clock3 className="size-3" /> Requested {asked.map(icon)}
        </span>
      )}
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
  const looked = useFacts(video.youtube_id, !video.views || !video.released).data;
  const views = video.views || looked?.views;
  const released = video.released || looked?.released;
  const meta = [views && `${views} views`, dated(released)].filter(Boolean).join(" · ");
  const soon = upcoming(released);
  const { handlers, preview, playing } = useHoverPreview(video);

  return (
    <Link
      to={`/watch/${video.id}`}
      {...handlers}
      onPointerEnter={(event) => { warm(); handlers.onPointerEnter?.(event); }}
      onPointerLeave={(event) => { cool(); handlers.onPointerLeave?.(event); }}
      onFocus={warm}
      onTouchStart={() => { prefetchVideo(video.id); handlers.onTouchStart?.(); }}
      className={cn("group flex min-w-0 flex-col gap-2.5 rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring", className)}
    >
      <div className="relative overflow-hidden rounded-xl ring-1 ring-white/5">
        <Thumb src={video.thumbnail} youtubeId={video.youtube_id} alt="" eager={eager}
               className="transition-transform duration-300 group-hover:scale-[1.03]" />
        {preview}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
        <span className={cn(playing && "hidden", "pointer-events-none absolute top-1/2 left-1/2 flex size-11 -translate-x-1/2 -translate-y-1/2 scale-90 items-center justify-center rounded-full bg-white/90 text-black opacity-0 shadow-xl transition-all group-hover:scale-100 group-hover:opacity-100")}>
          <Play className="ml-0.5 size-5 fill-current" />
        </span>
        <div className="absolute top-2 left-2 flex gap-1">
          {soon && <TagBadge tag="soon" look="overlay" tone="accent" compact />}
          {video.source === "apple" && <TagBadge tag="apple" look="overlay" compact />}
          {video.explicit && <TagBadge tag="explicit" look="overlay" compact />}
          {!video.official && <TagBadge tag="fan-upload" look="overlay" />}
        </div>
        <StatusMark video={video} requested={requested} />
        {!!video.duration && !soon && (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
            {clock(video.duration)}
          </span>
        )}
      </div>
      <div className="min-w-0 px-0.5">
        <Marquee text={video.title} className="text-sm leading-snug font-medium text-foreground/95 group-hover:text-foreground" />
        <Marquee text={video.artist} className="mt-0.5 text-[13px] text-muted-foreground" />
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
