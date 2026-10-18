import { useEffect, useState } from "react";

import type { Job } from "@/lib/api";
import { clock, cn, formatBytes, formatSpeed, timeLeft } from "@/lib/utils";

import { STAGE, STEP } from "./labels";

/** The time now, a second at a time, while active. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

/** Where a queued or running job is: a bar and a line of figures. */
export function JobProgress({ job, className }: { job: Job; className?: string }) {
  const now = useNow(job.status === "running");
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
  const stage = progress?.stage ?? job.stage ?? "starting";
  const step = progress?.step ? STEP[progress.step] ?? progress.step : null;
  const since = progress?.step_since ? clock(now - progress.step_since) : null;
  const figures = stage !== "downloading" ? [
    progress?.items && progress.items > 1 ? `video ${progress.item} of ${progress.items}` : null,
    progress?.total_bytes ? `${formatBytes(progress.total_bytes)} file` : null,
    since ? `for ${since}` : null,
  ].filter(Boolean) : [
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
        {/* Working, with no share to show: a piece of bar that moves. */}
        <div className={cn("h-full rounded-full bg-gradient-to-r from-sky-500 to-cyan-400 transition-[width] duration-700",
                           busy && "progress-indeterminate")}
             style={busy ? undefined : { width: `${Math.max(2, percent ?? 0)}%` }} />
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">
        {step ?? STAGE[stage]}{percent !== null && stage === "downloading" ? ` · ${percent.toFixed(0)}%` : ""}
        {figures.length ? ` · ${figures.join(" · ")}` : ""}
      </p>
    </div>
  );
}
