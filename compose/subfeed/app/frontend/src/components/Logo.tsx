import { useId } from "react";

import { cn } from "@/lib/utils";

/** A feed's waves, orange to amber; the favicon is the same drawing. */
export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fb923c" />
          <stop offset="1" stopColor="#fcd34d" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id})`} />
      <circle cx="20" cy="44" r="5" fill="#1c0f06" />
      <path d="M15 29a20 20 0 0 1 20 20M15 16a33 33 0 0 1 33 33" fill="none" stroke="#1c0f06" strokeWidth="6" strokeLinecap="round" />
    </svg>
  );
}
