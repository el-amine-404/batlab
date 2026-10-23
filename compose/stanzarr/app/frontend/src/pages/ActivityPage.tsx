import { Activity, CircleCheck, CircleX, Cpu, Loader2, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router";

import { Pager } from "@/components/library/SongTable";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import type { ActivityFilters } from "@/lib/api";
import { useActivity, useActivityNow } from "@/lib/queries";
import { useDebounced } from "@/lib/useDebounced";
import { cn, timeAgo } from "@/lib/utils";

const KINDS: { value: string; label: string }[] = [
  { value: "", label: "Everything" },
  { value: "lyrics", label: "Lyrics written" },
  { value: "search", label: "Searches" },
  { value: "filter", label: "Filtered" },
  { value: "ai", label: "AI" },
  { value: "workers", label: "Workers" },
];

/** What runs now: the scan or the search and how far, the AI jobs, the workers online. */
function Now() {
  const { data } = useActivityNow();
  if (!data) return null;
  const { busy, progress, jobs, workers_online: workers, last_scan: scan } = data;
  const running = jobs.filter((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued");
  return (
    <Card className="mb-6 flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Now</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Cpu className="size-3.5" />
          {workers.length ? `${workers.map((w) => w.name).join(", ")} online` : <Link to="/settings/workers" className="hover:underline">No worker online</Link>}
        </span>
      </div>
      {busy ? (
        <div className="flex flex-col gap-2">
          <p className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin text-primary" />
            {busy[0].toUpperCase() + busy.slice(1)}
            {progress.total ? `: ${progress.done ?? 0} of ${progress.total} songs` : progress.seen ? `: ${progress.seen.toLocaleString()} songs read` : ""}
          </p>
          {progress.total ? <Progress value={((progress.done ?? 0) / progress.total) * 100} /> : null}
          {progress.folder && <p className="truncate text-xs text-muted-foreground">{progress.folder}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Resting.{scan.at ? ` Last scan ${timeAgo(scan.at)}: ${scan.added ?? 0} added, ${scan.changed ?? 0} changed, ${scan.removed ?? 0} removed.` : ""}
        </p>
      )}
      {(running.length > 0 || queued.length > 0) && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-3 text-sm">
          {running.map((job) => (
            <p key={job.id} className="flex items-center gap-2">
              <Loader2 className="size-3.5 animate-spin text-primary" />
              <Link to={`/songs/${job.song_id}`} className="truncate font-medium hover:underline">{job.artist} – {job.title}</Link>
              <span className="shrink-0 text-xs text-muted-foreground">AI sync, {job.stage || "starting"} on {job.worker}</span>
            </p>
          ))}
          {queued.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {queued.length} waiting for a GPU: {queued.slice(0, 3).map((j) => j.title).join(", ")}{queued.length > 3 ? "…" : ""}{" "}
              <Link to="/queue" className="text-primary hover:underline">AI queue</Link>
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

/**
 * Activity: what runs now, then everything stanzarr did, newest first: who
 * asked (a person, an app's key, a worker; nobody: stanzarr itself), what
 * happened, what failed and why.
 */
export function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const typed = params.get("q") ?? "";
  const q = useDebounced(typed.trim(), 250);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const filters: ActivityFilters = {
    kind: params.get("kind") ?? undefined, q: q || undefined,
    ok: params.get("failed") === "1" ? false : undefined,
    song_id: Number(params.get("song")) || undefined,
  };
  const log = useActivity(filters, page);
  const set = (values: Record<string, string | undefined>, replace = false) =>
    setParams((old) => {
      const next = new URLSearchParams(old);
      for (const [k, v] of Object.entries(values)) v ? next.set(k, v) : next.delete(k);
      if (!("page" in values)) next.delete("page");
      return next;
    }, { replace });

  return (
    <>
      <PageHeader title="Activity" description="What stanzarr is doing, and what it did: who asked, what was written, what failed." />
      <Now />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Kind">
          {KINDS.map((k) => (
            <button key={k.value} type="button" onClick={() => set({ kind: k.value || undefined })} aria-pressed={(filters.kind ?? "") === k.value}
                    className={cn("rounded-full px-3 py-1 text-xs hover:bg-accent", (filters.kind ?? "") === k.value && "bg-accent text-foreground ring-1 ring-border")}>
              {k.label}
            </button>
          ))}
          <button type="button" onClick={() => set({ failed: filters.ok === false ? undefined : "1" })} aria-pressed={filters.ok === false}
                  className={cn("flex items-center gap-1 rounded-full px-3 py-1 text-xs hover:bg-accent", filters.ok === false && "bg-destructive/15 text-destructive ring-1 ring-destructive/30")}>
            <CircleX className="size-3" /> Failed only{log.data?.failed_today ? ` (${log.data.failed_today} today)` : ""}
          </button>
        </div>
        <div className="relative ml-auto min-w-52 flex-1 sm:max-w-72">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" value={typed} onChange={(e) => set({ q: e.target.value || undefined }, true)} placeholder="A song, a person, an error"
                 className="pl-9" aria-label="Search the activity" />
        </div>
      </div>
      {filters.song_id && (
        <p className="mb-3 text-xs text-muted-foreground">
          One song only. <button type="button" className="text-primary hover:underline" onClick={() => set({ song: undefined })}>Show every song</button>
        </p>
      )}
      {log.isPending ? <Loading /> : log.error ? <Failed error={log.error} retry={() => void log.refetch()} /> : !log.data.events.length ? (
        <Empty icon={<Activity />} title="Nothing here">Nothing happened like that yet.</Empty>
      ) : (
        <div className={cn(log.isPlaceholderData && "opacity-60")}>
          <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
            {log.data.events.map((event) => (
              <li key={event.id} className="flex items-start gap-3 px-4 py-2.5 text-sm">
                {event.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-muted-foreground/60" aria-label="Went well" />
                  : <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" aria-label="Failed" />}
                <div className="min-w-0 flex-1">
                  <p className="break-words">
                    {event.song_id ? <Link to={`/songs/${event.song_id}`} className="hover:underline">{event.message}</Link> : event.message}
                  </p>
                  {event.detail && <p className="mt-0.5 text-xs break-words text-muted-foreground">{event.detail}</p>}
                </div>
                <span className="hidden w-28 shrink-0 truncate text-right text-xs text-muted-foreground sm:block" title="Who asked">
                  {event.actor || "stanzarr"}
                </span>
                <span className="w-16 shrink-0 text-right text-xs text-muted-foreground" title={new Date(event.at * 1000).toLocaleString()}>
                  {timeAgo(event.at)}
                </span>
              </li>
            ))}
          </ul>
          <Pager page={page} size={log.data.size} total={log.data.total} onPage={(n) => set({ page: n > 1 ? String(n) : undefined })} />
        </div>
      )}
    </>
  );
}
