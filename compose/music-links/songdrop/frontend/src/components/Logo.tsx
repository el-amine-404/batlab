import { cn } from "@/lib/utils";

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0 drop-shadow-[0_8px_24px_rgba(168,85,247,0.35)]", className)} aria-hidden>
      <defs>
        <linearGradient id="songdrop-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#d946ef" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#songdrop-logo)" />
      <g fill="none" stroke="#fff" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M25 44V18l20-4v26" />
        <circle cx="19.5" cy="44" r="5.5" fill="#fff" />
        <circle cx="39.5" cy="40" r="5.5" fill="#fff" />
      </g>
    </svg>
  );
}
