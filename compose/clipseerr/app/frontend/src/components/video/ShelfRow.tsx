import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import type { Shelf } from "@/lib/api";

import { useRequested } from "./useRequested";
import { CardSkeleton, VideoCard } from "./VideoCard";

const CARD = "w-[72vw] max-w-[300px] shrink-0 sm:w-[260px] lg:w-[280px]";

/** A shelf as a row that scrolls sideways, with arrows on wide screens. */
export function ShelfRow({ shelf, eager = false }: { shelf: Shelf; eager?: boolean }) {
  const rail = useRef<HTMLDivElement>(null);
  const requested = useRequested();
  const [edges, setEdges] = useState({ start: true, end: false });

  const measure = useCallback(() => {
    const el = rail.current;
    if (!el) return;
    setEdges({ start: el.scrollLeft < 8, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 8 });
  }, []);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, shelf.items.length]);

  const scroll = (direction: 1 | -1) => {
    const el = rail.current;
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.85, behavior: "smooth" });
  };

  return (
    <section className="group/shelf" aria-labelledby={`shelf-${shelf.id}`}>
      <div className="mb-3 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 id={`shelf-${shelf.id}`} className="truncate text-lg font-semibold tracking-tight">
            {shelf.title}
          </h2>
          {shelf.subtitle && <p className="truncate text-xs text-muted-foreground">{shelf.subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {shelf.total > shelf.items.length && (
            <Link to={`/shelf/${shelf.id}`} className="mr-1 rounded-md px-2 py-1 text-sm font-medium text-rose-300 hover:bg-accent">
              See all {shelf.total}
            </Link>
          )}
          <button type="button" onClick={() => scroll(-1)} disabled={edges.start} aria-label="Scroll back"
                  className="hidden size-8 items-center justify-center rounded-full border border-border text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:opacity-30 sm:flex">
            <ChevronLeft className="size-4" />
          </button>
          <button type="button" onClick={() => scroll(1)} disabled={edges.end} aria-label="Scroll on"
                  className="hidden size-8 items-center justify-center rounded-full border border-border text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:opacity-30 sm:flex">
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>
      <div ref={rail} onScroll={measure} className="rail -mx-4 flex gap-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
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
