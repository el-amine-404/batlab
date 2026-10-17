import { useNow } from "@/lib/useNow";
import { timeAgo } from "@/lib/utils";

/** "Updated 9 min ago": when the list's oldest part was fetched from its source. */
export function Updated({ at, className }: { at?: number | null; className?: string }) {
  const now = useNow();
  if (!at) return null;
  return (
    <span className={className} title={new Date(at * 1000).toLocaleString()}>
      Updated {timeAgo(at, now)}
    </span>
  );
}
