import { type ReactNode, useId, useState } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * One line on what something means, shown while the pointer is on it or it
 * has the keyboard's focus. Drawn over the page, not inside its parent: a
 * card's picture clips what it holds, and the badges sit in it.
 */
export function Hint({ text, children, className, wide = false }: {
  text: ReactNode;
  children: ReactNode;
  className?: string;
  /** For a few lines, not one. */
  wide?: boolean;
}) {
  const id = useId();
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const show = (target: HTMLElement) => {
    const box = target.getBoundingClientRect();
    setAt({ x: Math.min(Math.max(box.left + box.width / 2, wide ? 180 : 140), window.innerWidth - (wide ? 180 : 140)), y: box.bottom + 6 });
  };
  return (
    <span
      className={cn("inline-flex", className)}
      aria-describedby={at ? id : undefined}
      onPointerEnter={(event) => show(event.currentTarget)}
      onPointerLeave={() => setAt(null)}
      onFocus={(event) => show(event.currentTarget)}
      onBlur={() => setAt(null)}
    >
      {children}
      {at && createPortal(
        <span id={id} role="tooltip" style={{ left: at.x, top: at.y }}
              className={cn("pointer-events-none fixed z-50 w-max -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs leading-snug text-popover-foreground shadow-lg",
                            wide ? "max-w-[min(22rem,calc(100vw-1rem))]" : "max-w-64")}>
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}
