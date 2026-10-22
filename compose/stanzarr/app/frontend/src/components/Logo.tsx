import { useId } from "react";

import { cn } from "@/lib/utils";

/** Lyric lines, each with its time in front: synced lyrics. Emerald to teal; the favicon is the same drawing. */
export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#34d399" />
          <stop offset="1" stopColor="#14b8a6" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id})`} />
      <g fill="#022c22">
        <circle cx="17" cy="21" r="3.5" />
        <circle cx="17" cy="32" r="3.5" />
        <circle cx="17" cy="43" r="3.5" />
      </g>
      <g fill="none" stroke="#022c22" strokeWidth="5.5" strokeLinecap="round">
        <path d="M27 21h21" />
        <path d="M27 32h14" />
        <path d="M27 43h18" />
      </g>
    </svg>
  );
}
