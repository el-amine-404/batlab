import { useId } from "react";

import { cn } from "@/lib/utils";

/** A play button dropping into a tray, sky to cyan; the favicon is the same drawing. */
export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id})`} />
      <path d="M25 14.5v21l17-10.5z" fill="#04121c" />
      <path d="M18 43v3.5a3.5 3.5 0 0 0 3.5 3.5h21a3.5 3.5 0 0 0 3.5-3.5V43" fill="none" stroke="#04121c" strokeWidth="5"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
