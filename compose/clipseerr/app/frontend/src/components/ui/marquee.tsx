import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * A line too long for its place: cut with an ellipsis, and read to its end
 * while its card (the nearest .group) is hovered or focused, then back to the
 * start. Rows of cards keep one line each, so they stay aligned.
 */
export function Marquee({ text, className }: { text: string; className?: string }) {
  const box = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);

  useLayoutEffect(() => {
    const outer = box.current;
    const inner = outer?.firstElementChild as HTMLElement | null;
    if (!outer || !inner) return;
    const measure = () => setShift(Math.max(0, inner.scrollWidth - outer.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(outer);
    return () => observer.disconnect();
  }, [text]);

  return (
    <span
      ref={box}
      title={shift ? text : undefined}
      data-overflow={shift ? "" : undefined}
      // About 40 px a second, never quicker than four seconds for the whole loop.
      style={{ "--marquee-shift": `-${shift}px`, "--marquee-time": `${Math.max(4, shift / 40 + 3)}s` } as React.CSSProperties}
      className={cn("marquee", className)}
    >
      <span>{text}</span>
    </span>
  );
}
