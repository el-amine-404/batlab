import { History, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { JobList } from "@/components/jobs/JobList";
import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { api, type Job } from "@/lib/api";
import { useJobs } from "@/lib/jobs";
import { cn } from "@/lib/utils";

const FILTERS: { key: string; label: string; test: (job: Job) => boolean }[] = [
  { key: "all", label: "All", test: () => true },
  { key: "finished", label: "Complete", test: (job) => job.status === "finished" },
  { key: "partial", label: "Songs missing", test: (job) => job.errors.length > 0 },
  { key: "failed", label: "Failed", test: (job) => job.status === "failed" },
  { key: "cancelled", label: "Cancelled", test: (job) => job.status === "cancelled" },
];

export function HistoryPage() {
  const { jobs, history, error, refresh } = useJobs();
  const [filter, setFilter] = useState("all");
  const [clearing, setClearing] = useState(false);

  const visible = useMemo(() => history.filter(FILTERS.find((f) => f.key === filter)!.test), [history, filter]);
  const clearable = history.filter((job) => job.status === "finished" || job.status === "cancelled");

  const clear = async () => {
    const count = `${clearable.length} download${clearable.length === 1 ? "" : "s"}`;
    if (!window.confirm(`Remove ${count} that are complete or cancelled? Songs already in your library stay.`)) return;
    setClearing(true);
    const results = await Promise.allSettled(clearable.map((job) => api.removeJob(job.id)));
    setClearing(false);
    const failed = results.filter((result) => result.status === "rejected").length;
    if (failed) toast.error(`${failed} couldn't be removed.`);
    refresh();
  };

  return (
    <>
      <PageHeader
        title="History"
        description="Downloads that ended. Each stays here, with its songs, for the time set in Settings, Downloads."
        actions={
          clearable.length > 0 && (
            <Button variant="secondary" size="sm" disabled={clearing} onClick={clear}>
              {clearing && <Loader2 className="animate-spin" />} Clear complete
            </Button>
          )
        }
      />
      <div role="tablist" aria-label="Show" className="mb-3 flex w-fit flex-wrap gap-1 rounded-xl border border-border bg-card p-1">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            role="tab"
            aria-selected={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
              filter === f.key && "bg-accent text-foreground",
            )}
          >
            {f.label}
            <span className="tabular-nums text-muted-foreground/80">{history.filter(f.test).length}</span>
          </button>
        ))}
      </div>
      {error && <Failed message={error} />}
      {!jobs && !error && <Loading />}
      {jobs && visible.length === 0 && <Empty icon={History} title={filter === "all" ? "No downloads yet" : "None of these"} />}
      {visible.length > 0 && <JobList key={filter} jobs={visible} startOpen={false} />}
    </>
  );
}
