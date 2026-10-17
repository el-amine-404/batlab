import { type ReactNode, useId, useState } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * One line on what something means, shown while the pointer is on it or it
 * has the keyboard's focus. Drawn over the page, not inside its parent: a
 * card's picture clips what it holds, and the badges sit in it.
 */
export function Hint({ text, children, className }: { text: ReactNode; children: ReactNode; className?: string }) {
  const id = useId();
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const show = (target: HTMLElement) => {
    const box = target.getBoundingClientRect();
    setAt({ x: Math.min(Math.max(box.left + box.width / 2, 140), window.innerWidth - 140), y: box.bottom + 6 });
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
              className="pointer-events-none fixed z-50 w-max max-w-64 -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs leading-snug text-popover-foreground shadow-lg">
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}
