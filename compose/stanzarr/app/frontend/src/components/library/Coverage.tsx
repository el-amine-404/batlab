import type { Coverage as Counts, LyricsStatus } from "@/lib/api";
import { STATUS, STATUS_ORDER } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The library at a glance: one bar, a part for each status, and the legend
 * under it; a part of the legend picks that status (again: all of them).
 */
export function Coverage({ counts, chosen, onChoose }: {
  counts: Counts;
  chosen?: LyricsStatus;
  onChoose: (status?: LyricsStatus) => void;
}) {
  const total = STATUS_ORDER.reduce((sum, status) => sum + counts[status], 0);
  const synced = counts.synced + counts.ai_corrected + counts.ai_checked + counts.ai_unchecked;
  const share = total ? Math.round((synced / total) * 100) : 0;
  return (
    <section className="rounded-2xl border border-border bg-card p-4 sm:p-5" aria-label="The library's coverage">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          <span className="text-2xl font-semibold text-foreground tabular-nums">{share}%</span> of {total.toLocaleString()} songs scroll
          in a player
        </p>
        {chosen && (
          <button type="button" onClick={() => onChoose(undefined)} className="text-xs text-primary hover:underline">
            Show every song
          </button>
        )}
      </div>
      <div className="mt-3 flex h-2.5 overflow-hidden rounded-full bg-muted" role="img"
           aria-label={STATUS_ORDER.map((s) => `${STATUS[s].label} ${counts[s]}`).join(", ")}>
        {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
          <span key={s} className={cn("h-full transition-opacity", STATUS[s].bar, chosen && chosen !== s && "opacity-30")}
                style={{ width: `${(counts[s] / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-1 gap-y-1">
        {STATUS_ORDER.map((s) => (
          <li key={s}>
            <button type="button" onClick={() => onChoose(chosen === s ? undefined : s)} title={STATUS[s].hint}
                    aria-pressed={chosen === s} disabled={!counts[s] && chosen !== s}
                    className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition hover:bg-accent disabled:opacity-40",
                                  chosen === s && "bg-accent text-foreground")}>
              <span className={cn("size-2 rounded-full", STATUS[s].dot)} aria-hidden />
              {STATUS[s].label} <span className="text-muted-foreground tabular-nums">{counts[s].toLocaleString()}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
