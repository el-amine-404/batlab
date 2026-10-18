import { Badge } from "@/components/ui/badge";
import type { Feed } from "@/lib/api";
import { cn, duration, timeAgo, timeLeft } from "@/lib/utils";

/** Off, failing, being read, or fine. */
export function FeedBadge({ feed }: { feed: Feed }) {
  if (!feed.enabled) return <Badge variant="muted">Off</Badge>;
  if (feed.failures) return <Badge variant="destructive">Failing</Badge>;
  if (!feed.last) return <Badge variant="accent">Reading…</Badge>;
  return <Badge variant="success">OK</Badge>;
}

/** How the last read went, and when the next one is. */
export function LastRead({ feed, className }: { feed: Feed; className?: string }) {
  return (
    <div className={cn("text-xs", className)}>
      {feed.last ? (
        <p className={feed.last.ok ? "text-muted-foreground" : "text-destructive"} title={feed.last.message ?? undefined}>
          Read {timeAgo(feed.last.at)}: {feed.last.message}
        </p>
      ) : (
        <p className="text-muted-foreground">Not read yet.</p>
      )}
      {feed.enabled && (
        <p className="mt-0.5 text-muted-foreground/70">
          Every {duration(feed.poll_minutes)}
          {feed.next_at ? ` · next ${timeLeft(feed.next_at)}` : " · next now"}
          {feed.failures > 0 && ` · ${feed.failures} failed in a row, waiting longer`}
        </p>
      )}
    </div>
  );
}
