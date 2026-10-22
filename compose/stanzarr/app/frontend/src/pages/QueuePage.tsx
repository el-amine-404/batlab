import { useQueryClient } from "@tanstack/react-query";
import { Cpu, ListOrdered, RotateCw, X } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, errorText, type Job } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { keys, useJobs, useStatus } from "@/lib/queries";
import { timeAgo } from "@/lib/utils";

const TONE: Record<string, "accent" | "success" | "destructive" | "muted" | "warning"> = {
  running: "accent", queued: "warning", finished: "success", failed: "destructive", cancelled: "muted",
};

/** The AI queue: what the GPU worker is doing, what waits, what ended. */
export function QueuePage() {
  const client = useQueryClient();
  const can = useCan();
  const { data, error, isPending, refetch } = useJobs();
  const { data: status } = useStatus();

  const run = async (work: () => Promise<unknown>) => {
    try {
      await work();
    } catch (caught) {
      toast.error(errorText(caught));
    }
    void client.invalidateQueries({ queryKey: keys.jobs });
  };

  const waiting = data?.some((job: Job) => job.status === "queued");
  return (
    <>
      <PageHeader title="AI queue" description="Songs sent to the GPU worker: plain lyrics placed in time, the unsure lines marked." />
      {status && !status.gpu_online && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-warning/30 bg-warning/10 p-3 text-sm">
          <Cpu className="size-4 text-warning" />
          <span className="flex-1">No GPU worker is online{waiting ? ": the queue waits for one." : "."}</span>
          <Button variant="secondary" size="sm" asChild><Link to="/settings/workers">Workers</Link></Button>
        </div>
      )}
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.length ? (
        <Empty icon={<ListOrdered />} title="Nothing queued">Open a song with plain lyrics and press "Sync with AI".</Empty>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
          {data.map((job) => (
            <li key={job.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <Link to={`/songs/${job.song_id}`} className="block truncate font-medium hover:underline">{job.title}</Link>
                <p className="truncate text-xs text-muted-foreground">
                  {job.artist}
                  {job.status === "running" && ` · ${job.stage || "starting"} on ${job.worker}`}
                  {job.status === "failed" && job.error && ` · ${job.error}`}
                  {job.status === "finished" && job.stage && ` · ${job.stage}`}
                </p>
              </div>
              <Badge variant={TONE[job.status] ?? "muted"}>{job.status}</Badge>
              <span className="hidden w-20 text-right text-xs text-muted-foreground sm:block">{timeAgo(job.finished_at ?? job.started_at ?? job.created_at)}</span>
              {can("ai") && (job.status === "queued" || job.status === "running") && (
                <Button variant="ghost" size="icon-sm" aria-label="Cancel" onClick={() => void run(() => api.cancelJob(job.id))}><X /></Button>
              )}
              {can("ai") && (job.status === "failed" || job.status === "cancelled") && (
                <Button variant="ghost" size="icon-sm" aria-label="Try again" onClick={() => void run(() => api.retryJob(job.id))}><RotateCw /></Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
