import { Link } from "react-router";

import type { LyricsStatus } from "@/lib/api";
import { STATUS } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Where a song's lyrics stand: a coloured dot and its name; the hint says
 * more. linked: a link to the library narrowed to that status.
 */
export function StatusBadge({ status, className, linked }: { status: LyricsStatus; className?: string; linked?: boolean }) {
  const s = STATUS[status];
  const body = (
    <>
      <span className="size-2 shrink-0 rounded-full" style={{ background: s.color }} aria-hidden /> {s.label}
    </>
  );
  const look = cn("inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap text-foreground/90", className);
  if (linked) {
    return (
      <Link to={`/library?status=${status}`} title={`${s.hint}. Show every song like this.`}
            className={cn(look, "rounded-full px-2 py-0.5 -mx-2 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none")}>
        {body}
      </Link>
    );
  }
  return <span className={look} title={s.hint}>{body}</span>;
}
