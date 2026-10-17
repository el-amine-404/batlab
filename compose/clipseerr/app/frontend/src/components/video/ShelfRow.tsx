import { Apple, CalendarDays, CalendarClock, Flame, ListMusic, type LucideIcon, Sparkles, TrendingUp, Trophy } from "lucide-react";
import { Link } from "react-router";

import { RailArrows, useRail } from "@/components/ui/rail";
import type { Shelf } from "@/lib/api";

import { Updated } from "./Updated";
import { useRequested } from "./useRequested";
import { CardSkeleton, VideoCard } from "./VideoCard";

const CARD = "w-[72vw] max-w-[300px] shrink-0 sm:w-[260px] lg:w-[280px]";

/** Which list a shelf is, at a glance: YouTube's new videos and charts, Apple's, the releases. */
export function shelfIcon(shelf: Pick<Shelf, "id" | "title">): LucideIcon {
  if (shelf.id === "yt-new") return Sparkles;
  if (shelf.id === "releases") return CalendarClock;
  if (shelf.id === "hot") return Flame;
  if (shelf.id.startsWith("am-")) return Apple;
  if (/^trending/i.test(shelf.title)) return TrendingUp;
  if (/^daily/i.test(shelf.title)) return CalendarDays;
  if (/^top/i.test(shelf.title)) return Trophy;
  return ListMusic;
}

/** A shelf as a row that scrolls sideways, with arrows on wide screens. */
export function ShelfRow({ shelf, eager = false }: { shelf: Shelf; eager?: boolean }) {
  const rail = useRail(shelf.items.length);
  const requested = useRequested();
  const Icon = shelfIcon(shelf);

  return (
    <section className="group/shelf" aria-labelledby={`shelf-${shelf.id}`}>
      <div className="mb-3 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 id={`shelf-${shelf.id}`} className="flex items-center gap-2 truncate text-lg font-semibold tracking-tight">
            <Icon className="size-4 shrink-0 text-rose-300" aria-hidden /> {shelf.title}
          </h2>
          <p className="truncate text-xs text-muted-foreground">
            {shelf.subtitle}
            {shelf.subtitle && shelf.updated_at ? " · " : ""}
            <Updated at={shelf.updated_at} />
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {shelf.total > shelf.items.length && (
            <Link to={`/shelf/${shelf.id}`} className="mr-1 rounded-md px-2 py-1 text-sm font-medium text-rose-300 hover:bg-accent">
              See all {shelf.total}
            </Link>
          )}
          <RailArrows rail={rail} />
        </div>
      </div>
      <div ref={rail.ref} onScroll={rail.measure} className="rail -mx-4 flex gap-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
        {shelf.items.map((video, index) => (
          <VideoCard key={video.id} video={video} className={CARD} requested={requested.get(video.id)} eager={eager && index < 4} />
        ))}
      </div>
    </section>
  );
}

export function ShelfSkeleton() {
  return (
    <section aria-hidden>
      <div className="skeleton mb-4 h-5 w-48 rounded" />
      <div className="flex gap-4 overflow-hidden">
        {Array.from({ length: 5 }, (_, i) => (
          <CardSkeleton key={i} className={CARD} />
        ))}
      </div>
    </section>
  );
}
