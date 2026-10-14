import { CloudOff, Inbox, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { JobRow } from "@/components/JobRow";
import { LinkForm } from "@/components/LinkForm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ACTIVE, api, type Job, RETRYING } from "@/lib/api";
import { useJobs } from "@/lib/hooks";
import { cn } from "@/lib/utils";

type Filter = "all" | "active" | "attention" | "done";

const FILTERS: { key: Filter; label: string; test: (job: Job) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  {
    key: "active",
    label: "Active",
    test: (job) => ACTIVE.includes(job.status) || job.errors.some((error) => RETRYING.includes(error.state)),
  },
  { key: "attention", label: "Missing songs", test: (job) => job.errors.length > 0 || job.status === "failed" },
  { key: "done", label: "Done", test: (job) => job.status === "done" },
];

const PAGE = 20;

export function DownloadsPage() {
  const { jobs, error, refresh } = useJobs();
  const [filter, setFilter] = useState<Filter>("all");
  const [shown, setShown] = useState(PAGE);
  const [clearing, setClearing] = useState(false);

  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map((f) => [f.key, jobs?.filter(f.test).length ?? 0])) as Record<Filter, number>,
    [jobs],
  );
  const visible = useMemo(() => {
    const test = FILTERS.find((f) => f.key === filter)!.test;
    return (jobs ?? []).filter(test);
  }, [jobs, filter]);
  const finished = (jobs ?? []).filter((job) => job.status === "done" || job.status === "cancelled");

  const clearFinished = async () => {
    if (!window.confirm(`Remove ${finished.length} finished download${finished.length === 1 ? "" : "s"} from the list? Songs already in your library stay.`)) return;
    setClearing(true);
    const results = await Promise.allSettled(finished.map((job) => api.remove(job.id)));
    setClearing(false);
    const failed = results.filter((result) => result.status === "rejected").length;
    if (failed) toast.error(`${failed} couldn't be removed.`);
    refresh();
  };

  return (
    <div className="flex flex-col gap-6">
      <LinkForm onCreated={refresh} />

      <section aria-labelledby="downloads" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 id="downloads" className="sr-only">
            Downloads
          </h1>
          <div role="tablist" aria-label="Show" className="flex flex-wrap gap-1 rounded-xl border border-border bg-card p-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                role="tab"
                aria-selected={filter === f.key}
                onClick={() => {
                  setFilter(f.key);
                  setShown(PAGE);
                }}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                  filter === f.key && "bg-accent text-foreground",
                )}
              >
                {f.label}
                <span className="tabular-nums text-muted-foreground/80">{counts[f.key]}</span>
              </button>
            ))}
          </div>
          {finished.length > 0 && (
            <Button variant="ghost" size="sm" disabled={clearing} onClick={clearFinished}>
              {clearing && <Loader2 className="animate-spin" />} Clear finished
            </Button>
          )}
        </div>

        {error && (
          <Card className="flex items-center gap-3 border-destructive/30 p-4 text-sm text-destructive">
            <CloudOff className="size-4 shrink-0" /> {error}
          </Card>
        )}

        {jobs && visible.length === 0 && (
          <Card className="flex flex-col items-center gap-2 px-6 py-10 text-center">
            <Inbox className="size-8 text-muted-foreground/60" />
            <p className="text-sm font-medium">{filter === "all" ? "Nothing here yet" : "Nothing in this list"}</p>
            {filter === "all" && (
              <p className="max-w-xs text-xs text-muted-foreground">
                Paste a song, album or playlist link above. Each song is checked and sent to your library.
              </p>
            )}
          </Card>
        )}

        {visible.length > 0 && (
          <Card className="divide-y divide-border overflow-hidden p-0">
            {visible.slice(0, shown).map((job) => (
              <JobRow key={job.id} job={job} onChange={refresh} />
            ))}
          </Card>
        )}

        {visible.length > shown && (
          <Button variant="secondary" className="self-center" onClick={() => setShown((n) => n + PAGE)}>
            Show {Math.min(PAGE, visible.length - shown)} more
          </Button>
        )}
      </section>
    </div>
  );
}
