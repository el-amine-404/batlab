import type { LyricsStatus } from "@/lib/api";
import { STATUS } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Where a song's lyrics stand: a coloured dot and its name; the hint says more. */
export function StatusBadge({ status, className }: { status: LyricsStatus; className?: string }) {
  const s = STATUS[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap", s.tone, className)} title={s.hint}>
      <span className={cn("size-1.5 shrink-0 rounded-full", s.dot)} aria-hidden /> {s.label}
    </span>
  );
}
