import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, Cpu, ListOrdered, Loader2, RotateCw, Sparkles, X, XCircle } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, errorText, type Job } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { keys, useJobs, useStatus } from "@/lib/queries";
import { formatDuration, getJobStageProgress, timeAgo } from "@/lib/utils";

const TONE: Record<string, "accent" | "success" | "destructive" | "muted" | "warning"> = {
  running: "accent",
  queued: "warning",
  finished: "success",
  failed: "destructive",
  cancelled: "muted",
};

/** A running job with animated multi-stage progress and time estimation. */
function RunningJobCard({ job, onCancel, canCancel }: { job: Job; onCancel: () => void; canCancel: boolean }) {
  const progress = getJobStageProgress(job.stage, job.kind, job.started_at);
  const elapsed = job.started_at ? Math.max(0, Math.round(Date.now() / 1000 - job.started_at)) : null;

  return (
    <div className="rounded-xl border border-primary/30 bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link to={`/songs/${job.song_id}`} className="truncate font-semibold text-foreground hover:underline">
              {job.title}
            </Link>
            <Badge variant="accent" className="flex items-center gap-1 py-0.5 text-[11px]">
              <Loader2 className="size-3 animate-spin" />
              <span>running</span>
            </Badge>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground uppercase font-mono">
              {job.kind === "ai_generate" ? "AI Generate" : "AI Sync"}
            </span>
          </div>
          <p className="truncate text-xs text-muted-foreground mt-0.5">
            {job.artist} · on <span className="font-medium text-foreground">{job.worker || "worker"}</span>
          </p>
        </div>
        {canCancel && (
          <Button variant="ghost" size="icon-sm" aria-label="Cancel job" onClick={onCancel}>
            <X className="size-4" />
          </Button>
        )}
      </div>

      {/* Progress Bar & Details */}
      <div className="mt-3.5 space-y-2">
        <div className="flex items-baseline justify-between text-xs">
          <span className="font-medium text-foreground">
            Step {progress.step} of {progress.totalSteps}: {progress.label}
          </span>
          <span className="font-mono text-xs font-semibold text-primary tabular-nums">{progress.percent}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-primary transition-all duration-500 ease-out"
            style={{ width: `${progress.percent}%` }}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <Clock className="size-3 text-muted-foreground/70" />
            <span>
              Elapsed: <span className="font-medium text-foreground">{elapsed !== null ? formatDuration(elapsed) : "starting"}</span>
            </span>
          </div>
          {progress.estimatedRemainingSecs ? (
            <span className="text-muted-foreground">
              Est. remaining:{" "}
              <span className="font-medium text-emerald-400">~{formatDuration(progress.estimatedRemainingSecs)}</span>
            </span>
          ) : (
            <span>Estimating remaining time...</span>
          )}
        </div>
      </div>
    </div>
  );
}

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
  const runningJobs = data?.filter((j) => j.status === "running") || [];
  const queuedJobs = data?.filter((j) => j.status === "queued") || [];
  const finishedJobs = data?.filter((j) => j.status !== "running" && j.status !== "queued") || [];

  return (
    <>
      <PageHeader
        title="AI queue"
        description="Songs sent to the GPU worker: plain lyrics placed in time, the unsure lines marked."
      />

      {status && !status.gpu_online && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-warning/30 bg-warning/10 p-3 text-sm">
          <Cpu className="size-4 text-warning" />
          <span className="flex-1">No GPU worker is online{waiting ? ": the queue waits for one." : "."}</span>
          <Button variant="secondary" size="sm" asChild>
            <Link to="/settings/workers">Workers</Link>
          </Button>
        </div>
      )}

      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.length ? (
        <Empty icon={<ListOrdered />} title="Nothing queued">
          Open a song with plain lyrics and press "Sync with AI", or press "Generate with AI".
        </Empty>
      ) : (
        <div className="space-y-6">
          {/* Active Running Section */}
          {runningJobs.length > 0 && (
            <div className="space-y-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
                <Sparkles className="size-4 text-emerald-400" />
                Active Processing ({runningJobs.length})
              </h2>
              <div className="grid gap-3 sm:grid-cols-1">
                {runningJobs.map((job) => (
                  <RunningJobCard
                    key={job.id}
                    job={job}
                    canCancel={can("ai")}
                    onCancel={() => void run(() => api.cancelJob(job.id))}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Queued Section */}
          {queuedJobs.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-semibold tracking-tight text-foreground">
                Waiting in Queue ({queuedJobs.length})
              </h2>
              <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
                {queuedJobs.map((job, idx) => (
                  <li key={job.id} className="flex items-center gap-3 px-4 py-3">
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
                      {idx + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <Link to={`/songs/${job.song_id}`} className="block truncate font-medium hover:underline">
                        {job.title}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">
                        {job.artist} · queued {timeAgo(job.created_at)}
                      </p>
                    </div>
                    <Badge variant="warning">queued</Badge>
                    {can("ai") && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Cancel"
                        onClick={() => void run(() => api.cancelJob(job.id))}
                      >
                        <X className="size-4" />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* History Section */}
          {finishedJobs.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-semibold tracking-tight text-foreground">
                Recent Jobs ({finishedJobs.length})
              </h2>
              <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
                {finishedJobs.map((job) => {
                  const tookSecs =
                    job.finished_at && job.started_at ? Math.max(0, job.finished_at - job.started_at) : null;
                  return (
                    <li key={job.id} className="flex items-center gap-3 px-4 py-3 hover:bg-muted/30">
                      <div className="shrink-0">
                        {job.status === "finished" ? (
                          <CheckCircle2 className="size-4 text-emerald-400" />
                        ) : job.status === "failed" ? (
                          <XCircle className="size-4 text-destructive" />
                        ) : (
                          <span className="size-4 rounded-full border border-muted-foreground/30 inline-block" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Link to={`/songs/${job.song_id}`} className="truncate font-medium hover:underline">
                            {job.title}
                          </Link>
                          <span className="text-xs text-muted-foreground font-normal">by {job.artist}</span>
                          {tookSecs !== null && (
                            <span className="rounded bg-muted/80 px-1.5 py-0.5 text-[11px] font-medium text-foreground">
                              took {formatDuration(tookSecs)}
                            </span>
                          )}
                        </div>
                        <p className="truncate text-xs text-muted-foreground mt-0.5">
                          {job.status === "failed" && job.error ? (
                            <span className="text-destructive">{job.error}</span>
                          ) : (
                            job.stage || (job.status === "finished" ? "completed" : "")
                          )}
                          {job.worker && <span className="text-muted-foreground/70"> · worker: {job.worker}</span>}
                        </p>
                      </div>
                      <Badge variant={TONE[job.status] ?? "muted"}>{job.status}</Badge>
                      <span className="hidden w-24 text-right text-xs text-muted-foreground sm:block tabular-nums">
                        {timeAgo(job.finished_at ?? job.started_at ?? job.created_at)}
                      </span>
                      {can("ai") && (job.status === "failed" || job.status === "cancelled") && (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Try again"
                          onClick={() => void run(() => api.retryJob(job.id))}
                        >
                          <RotateCw className="size-4" />
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </>
  );
}
