import { ArrowRight, CalendarClock } from "lucide-react";
import { useRef } from "react";
import { Link } from "react-router";

import type { Video } from "@/lib/api";
import { Marquee } from "@/components/ui/marquee";
import { prefetchVideo, useFacts } from "@/lib/queries";
import { ago, cn, day, daysFrom } from "@/lib/utils";

import { Thumb } from "./Thumb";
import { Updated } from "./Updated";

export type DayGroup = { key: string; label: string; items: Video[] };

/** Releases by day: "Coming soon", "Today", "Yesterday", "3 days ago"… in the list's order (newest first). */
export function byDay(items: Video[]): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const video of items) {
    const days = video.released ? daysFrom(video.released) : null;
    const key = days === null ? "undated" : days > 0 ? "soon" : String(days);
    const label = key === "soon" ? "Coming soon" : days === 0 ? "Today" : days === -1 ? "Yesterday" : days === null ? "Undated" : ago(video.released);
    const last = groups[groups.length - 1];
    if (last?.key === key) last.items.push(video);
    else groups.push({ key, label, items: [video] });
  }
  return groups;
}

function Row({ video }: { video: Video }) {
  const timer = useRef<number | undefined>(undefined);
  const soon = (daysFrom(video.released ?? "") ?? 0) > 0;
  const looked = useFacts(video.youtube_id, !video.views).data;
  const views = video.views || looked?.views;
  return (
    <li>
      <Link
        to={`/watch/${video.id}`}
        onPointerEnter={() => {
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => prefetchVideo(video.id), 120);
        }}
        onPointerLeave={() => window.clearTimeout(timer.current)}
        className="group flex items-center gap-3 rounded-lg p-1.5 outline-none transition hover:bg-accent/60 focus-visible:ring-[3px] focus-visible:ring-ring"
      >
        <div className="relative w-24 shrink-0 overflow-hidden rounded-md ring-1 ring-white/5">
          <Thumb src={video.thumbnail} youtubeId={video.youtube_id} />
          {video.explicit && (
            <span className="absolute top-1 left-1 rounded bg-black/65 px-1 text-[9px] font-semibold text-white/90" title="Explicit">E</span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <Marquee text={video.title} className="text-[13px] leading-snug font-medium text-foreground/95" />
          <Marquee text={video.artist} className="text-xs text-muted-foreground" />
          <p className={cn("truncate text-[11px]", soon ? "text-rose-300" : "text-muted-foreground/70")} title={day(video.released)}>
            {soon ? `Out ${day(video.released)} · ${ago(video.released)}` : day(video.released)}
            {views && ` · ${views} views`}
            {video.source === "apple" && <span className="text-muted-foreground/50"> · Apple</span>}
          </p>
        </div>
      </Link>
    </li>
  );
}

/** Discover's right column: the newest releases, by day, as a list that scrolls on its own. */
export function ReleaseRail({ items, total, updatedAt, isPending, limit = 60, className }: {
  items?: Video[];
  total?: number;
  updatedAt?: number | null;
  isPending: boolean;
  limit?: number;
  className?: string;
}) {
  const groups = byDay((items ?? []).slice(0, limit));
  return (
    <section aria-labelledby="releases" className={cn("flex min-h-0 flex-col rounded-2xl border border-border bg-card/50", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 id="releases" className="flex items-center gap-2 font-semibold tracking-tight">
            <CalendarClock className="size-4 text-rose-300" /> Latest releases
          </h2>
          <Updated at={updatedAt} className="block truncate text-[11px] text-muted-foreground/70" />
        </div>
        <Link to="/releases" className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-rose-300 hover:bg-accent">
          All {total || ""} <ArrowRight className="size-3" />
        </Link>
      </div>
      <div className="rail min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {isPending ? (
          <div className="flex flex-col gap-2 p-1.5" aria-hidden>
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="flex gap-3">
                <div className="skeleton aspect-video w-24 rounded-md" />
                <div className="flex flex-1 flex-col gap-1.5 pt-1">
                  <div className="skeleton h-3 w-4/5 rounded" />
                  <div className="skeleton h-2.5 w-1/2 rounded" />
                </div>
              </div>
            ))}
          </div>
        ) : !groups.length ? (
          <p className="p-3 text-sm text-muted-foreground">Nothing came out lately, or the sources didn't answer.</p>
        ) : (
          groups.map((group) => (
            <div key={group.key} className="mb-2">
              <p className={cn("sticky top-0 z-10 bg-card/95 px-1.5 py-1 text-[11px] font-semibold tracking-wider uppercase backdrop-blur",
                               group.key === "soon" ? "text-rose-300" : "text-muted-foreground")}>
                {group.label}
              </p>
              <ul>
                {group.items.map((video) => (
                  <Row key={video.id} video={video} />
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
