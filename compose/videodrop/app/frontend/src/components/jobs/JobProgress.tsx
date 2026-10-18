import type { Job } from "@/lib/api";
import { clock, cn, formatBytes, formatSpeed, timeLeft } from "@/lib/utils";

import { STAGE } from "./labels";

/** Where a queued or running job is: a bar and a line of figures. */
export function JobProgress({ job, className }: { job: Job; className?: string }) {
  if (job.status === "queued") {
    return (
      <p className={cn("text-xs text-muted-foreground", className)}>
        {job.retry_at ? <>Trying again {timeLeft(job.retry_at)}{job.error && <span className="text-warning"> · last try: {job.error}</span>}</> : "Waiting its turn"}
      </p>
    );
  }
  if (job.status !== "running") return null;
  const progress = job.progress;
  const percent = progress?.percent ?? null;
  const stage = job.stage ?? progress?.stage ?? "starting";
  const figures = [
    progress?.items && progress.items > 1 ? `video ${progress.item} of ${progress.items}` : null,
    stage === "downloading" && progress?.downloaded_bytes ? `${formatBytes(progress.downloaded_bytes)}${progress.total_bytes ? ` of ${formatBytes(progress.total_bytes)}` : ""}` : null,
    stage === "downloading" && progress?.speed ? formatSpeed(progress.speed) : null,
    stage === "downloading" && progress?.eta ? `${clock(progress.eta)} left` : null,
  ].filter(Boolean);
  const busy = stage !== "downloading" || percent === null;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="h-1.5 overflow-hidden rounded-full bg-secondary" role="progressbar" aria-valuemin={0} aria-valuemax={100}
           aria-valuenow={percent ?? undefined} aria-label={STAGE[stage]}>
        <div className={cn("h-full rounded-full bg-gradient-to-r from-sky-500 to-cyan-400 transition-[width] duration-700",
                           busy && "skeleton w-full opacity-60")}
             style={busy ? undefined : { width: `${Math.max(2, percent ?? 0)}%` }} />
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">
        {STAGE[stage]}{percent !== null && stage === "downloading" ? ` · ${percent.toFixed(0)}%` : ""}
        {figures.length ? ` · ${figures.join(" · ")}` : ""}
      </p>
    </div>
  );
}
