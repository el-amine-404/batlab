import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/** A row that scrolls sideways: its ref, whether it is at either end, and a step either way. */
export function useRail(count: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: false });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setEdges({ start: el.scrollLeft < 8, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 8 });
  }, []);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, count]);

  const scroll = (direction: 1 | -1) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.85, behavior: "smooth" });
  };
  return { ref, edges, measure, scroll };
}

const ARROW = "hidden size-8 items-center justify-center rounded-full border border-border text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:opacity-30 sm:flex";

export function RailArrows({ rail }: { rail: ReturnType<typeof useRail> }) {
  return (
    <>
      <button type="button" onClick={() => rail.scroll(-1)} disabled={rail.edges.start} aria-label="Scroll back" className={ARROW}>
        <ChevronLeft className="size-4" />
      </button>
      <button type="button" onClick={() => rail.scroll(1)} disabled={rail.edges.end} aria-label="Scroll on" className={ARROW}>
        <ChevronRight className="size-4" />
      </button>
    </>
  );
}
