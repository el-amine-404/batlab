import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Pixels a second it moves at. */
const SPEED = 45;
const REST_AT_START = 2500;
const REST_AT_END = 1500;

const pause = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });

/**
 * One line of text too long for its place: cut with "…" at first, then
 * slowly moved to show its end, back to its start, and again. Held still
 * (and cut) while the pointer is on it, which shows it whole in a tooltip;
 * never moved for someone who asked their system for less motion. A text
 * that fits just sits there.
 */
export function ScrollingText({ text, href, className, children }: {
  text: string;
  /** Makes it a link (opened in a new tab). */
  href?: string;
  className?: string;
  /** What is shown, when it isn't text itself. */
  children?: ReactNode;
}) {
  const box = useRef<HTMLElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);
  const [moving, setMoving] = useState(false);
  const [held, setHeld] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setOverflow(Math.max(0, Math.ceil(el.scrollWidth - el.clientWidth)));
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, [text]);

  useEffect(() => {
    const span = inner.current;
    if (!span || overflow < 4 || held || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const stop = new AbortController();
    let animation: Animation | undefined;
    void (async () => {
      while (!stop.signal.aborted) {
        await pause(REST_AT_START, stop.signal);
        if (stop.signal.aborted) break;
        setMoving(true);
        animation = span.animate([{ transform: "translateX(0)" }, { transform: `translateX(-${overflow}px)` }],
                                 { duration: Math.max(1200, (overflow / SPEED) * 1000), easing: "ease-in-out", fill: "forwards" });
        await Promise.race([animation.finished.catch(() => undefined), pause(1e9, stop.signal)]);
        await pause(REST_AT_END, stop.signal);
        if (stop.signal.aborted) break;
        animation = span.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: "forwards" });
        await animation.finished.catch(() => undefined);
        for (const running of span.getAnimations()) running.cancel();
        setMoving(false);
        span.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
      }
    })();
    return () => {
      stop.abort();
      for (const running of span.getAnimations()) running.cancel();
      setMoving(false);
    };
  }, [overflow, held]);

  const hold = { onPointerEnter: () => setHeld(true), onPointerLeave: () => setHeld(false), onFocus: () => setHeld(true), onBlur: () => setHeld(false) };
  const classes = cn("block min-w-0 overflow-hidden whitespace-nowrap", !moving && "text-ellipsis", className);
  const content = <span ref={inner} className={moving ? "inline-block" : undefined}>{children ?? text}</span>;
  const line = href ? (
    <a ref={box as RefObject<HTMLAnchorElement>} href={href} target="_blank" rel="noreferrer" className={classes} {...hold}>{content}</a>
  ) : (
    <span ref={box as RefObject<HTMLSpanElement>} tabIndex={overflow ? 0 : undefined} className={classes} {...hold}>{content}</span>
  );
  return (
    <Tooltip content={<span className="font-mono break-all">{text}</span>} disabled={!overflow}
             className="max-w-[min(36rem,calc(100vw-2rem))]">
      {line}
    </Tooltip>
  );
}
