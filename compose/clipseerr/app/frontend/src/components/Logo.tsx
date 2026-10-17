import { useId } from "react";

import { cn } from "@/lib/utils";

/** A screen with a play mark, rose to amber; the favicon is the same drawing. */
export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fb7185" />
          <stop offset="1" stopColor="#fdba74" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id})`} />
      <rect x="12" y="18" width="40" height="28" rx="7" fill="none" stroke="#1a0b10" strokeWidth="4" />
      <path d="M28 25.5v13l11-6.5z" fill="#1a0b10" />
    </svg>
  );
}
