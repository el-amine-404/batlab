import { cn } from "@/lib/utils";

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <rect width="64" height="64" rx="16" fill="#0f766e" />
      <path d="M16 25v-9h9M39 16h9v9M48 39v9h-9M25 48h-9v-9" fill="none" stroke="#ccfbf1" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M24 22h12l5 5v15H24z" fill="#f0fdfa" />
      <path d="M29 31h8M29 36h8" stroke="#0f766e" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
