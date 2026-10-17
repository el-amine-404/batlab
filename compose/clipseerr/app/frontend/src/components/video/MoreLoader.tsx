import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

import { GridSkeleton } from "./VideoGrid";

/** How far below the screen the next page starts loading: about two screens, so scrolling rarely waits. */
const AHEAD = "1600px 0px";

/**
 * Calls onVisible when the returned element is within AHEAD of the screen;
 * again each time again changes (a page came in and the end is still near).
 */
function useNearby(onVisible: () => void, enabled: boolean, again: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const callback = useRef(onVisible);
  callback.current = onVisible;
  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const observer = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && callback.current(), {
      rootMargin: AHEAD,
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [enabled, again]);
  return ref;
}

/**
 * The end of a list read a page at a time: the next page loads on its own as
 * the end comes near, while skeleton cards hold its place; a button does the
 * same for keyboards and for when it fails.
 */
export function MoreLoader({ shown, total, hasMore, loading, failed, load }: {
  shown: number;
  total: number;
  hasMore: boolean;
  loading: boolean;
  failed?: boolean;
  load: () => void;
}) {
  const ref = useNearby(() => {
    if (hasMore && !loading && !failed) load();
  }, hasMore && !failed && !loading, shown);

  return (
    <div className="mt-8 flex flex-col items-center gap-4">
      {loading && <GridSkeleton count={8} />}
      <div ref={ref} className="flex flex-col items-center gap-3 text-center" aria-live="polite">
        <p className="text-xs text-muted-foreground tabular-nums">
          {hasMore ? `${shown} of ${total}` : total > 0 ? `All ${total} shown` : ""}
        </p>
        {hasMore && (
          <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
            {loading && <Loader2 className="animate-spin" />}
            {failed ? "Couldn't load more: try again" : loading ? "Loading" : "Show more"}
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * A long list already loaded, drawn a step at a time as it scrolls (an
 * artist's 200 videos): the first screen is quick, the rest come as needed.
 * Starts again when reset changes (a new filter or order).
 */
export function useReveal(total: number, step = 48, reset?: unknown) {
  const [count, setCount] = useState(step);
  useEffect(() => setCount(step), [reset, step]);
  const more = useCallback(() => setCount((n) => Math.min(total, n + step)), [total, step]);
  return { count: Math.min(count, total), more, hasMore: count < total };
}
