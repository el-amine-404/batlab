import { Hint } from "@/components/ui/hint";
import type { Checks } from "@/lib/api";
import { useNow } from "@/lib/useNow";
import { elapsed, timeAgo, timeLeft } from "@/lib/utils";

const at = (seconds: number) => new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

/** "3 new", "2 new, 1 gone", "1 gone". */
function counted(added: number, removed: number): string {
  return [added && `${added} new`, removed && `${removed} gone`].filter(Boolean).join(", ") || "changed";
}

/**
 * A list's checks in one line, "checked 11 min ago, no change for 2 d · next
 * in 19 min" (or "…: 3 new" when that check changed it); on hover, each of its
 * sources: when read, what it found, when read next.
 */
export function Checked({ checks }: { checks: Checks }) {
  const now = useNow();
  // The check that changed the list is the one that counted it, within a moment.
  const fresh = checks.changed_at != null && Math.abs(checks.changed_at - checks.checked_at) < 60;
  const change = fresh ? `: ${counted(checks.added ?? 0, checks.removed ?? 0)}`
    : checks.changed_at ? `, no change for ${elapsed(checks.changed_at, now)}` : "";
  const next = checks.next_at == null ? "" : checks.next_at > now ? ` · next in ${timeLeft(checks.next_at, now)}` : " · checking now";
  const searches = checks.searches ?? 0;

  const details = (
    <span className="flex flex-col gap-1.5">
      <span>
        Checked {at(checks.checked_at)}: clipseerr makes the list again from its sources about every 30 min
        {checks.next_at ? `, next at ${new Date(checks.next_at * 1000).toLocaleTimeString([], { timeStyle: "short" })}` : ""}.
      </span>
      <span>
        {checks.changed_at
          ? `Last change ${at(checks.changed_at)}: ${counted(checks.added ?? 0, checks.removed ?? 0)}.`
          : "No change seen since it began counting."}
      </span>
      {checks.sources?.map((source) => (
        <span key={source.name}>
          <span className="font-medium">{source.name}</span>
          {source.checked_at ? `: read ${timeAgo(source.checked_at, now)}` : ""}
          {source.message ? `, ${source.ok ? "" : "failed: "}${source.message}` : ""}.
          {source.next_at && source.next_at > now ? ` Next read in ${timeLeft(source.next_at, now)}.` : ""}
          {source.changed_at ? ` Last new post ${timeAgo(source.changed_at, now)}.` : ""}
        </span>
      ))}
      {searches > 0 && <span>And {searches} YouTube {searches === 1 ? "search" : "searches"}, each read every 6 h.</span>}
      {checks.sources?.length ? <span className="text-muted-foreground">A new post's video joins once clipseerr has judged it a real song.</span> : null}
    </span>
  );
  return (
    <Hint text={details} wide>
      <span tabIndex={0} className="cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-4">
        checked {timeAgo(checks.checked_at, now)}{change}{next}
      </span>
    </Hint>
  );
}
