import { ChevronDown, CloudUpload, FileImage, Loader2, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { Project } from "@/lib/api";
import { cancelUpload, retryUpload, type Upload, useUploads } from "@/lib/uploads";
import { cn, eta, formatBytes, plural } from "@/lib/utils";

import { STAGE, STEPS } from "./labels";

/** Ticks once a second while it matters: what is left counts down between the server's answers. */
function useCountdown(seconds: number | undefined, measuredAt: number) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (seconds === undefined) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [seconds]);
  if (seconds === undefined) return undefined;
  return Math.max(1, seconds - Math.max(0, (now - measuredAt) / 1000));
}

function UploadRow({ item }: { item: Upload }) {
  return (
    <li className="flex items-center gap-3 py-1.5">
      <span className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-md bg-tile">
        {item.preview ? <img src={item.preview} alt="" className="size-full object-cover" /> : <FileImage className="size-4 text-muted-foreground" aria-hidden />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{item.file.name}</span>
        {item.status === "failed" ? (
          <span className="block text-xs text-destructive">{item.error}</span>
        ) : (
          <span className="mt-1 block h-1 overflow-hidden rounded-full bg-muted">
            <span className="block h-full rounded-full bg-primary transition-[width]" style={{ width: `${Math.round(item.sent * 100)}%` }} />
          </span>
        )}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{formatBytes(item.file.size)}</span>
      {item.status === "failed" && <Button variant="secondary" size="icon-sm" aria-label={`Send ${item.file.name} again`} onClick={() => retryUpload(item.id)}><RotateCcw /></Button>}
      <Button variant="ghost" size="icon-sm" aria-label={`Cancel ${item.file.name}`} onClick={() => cancelUpload(item.id)}><X /></Button>
    </li>
  );
}

/** What is happening to this scan's photos, in words, step by step, and how long is left. */
export function Pipeline({ project, measuredAt }: { project: Project; measuredAt: number }) {
  const uploads = useUploads(project.public_id);
  const progress = project.progress;
  const left = useCountdown(progress?.eta_seconds, measuredAt);
  const [open, setOpen] = useState(false);
  if (!progress && !uploads.length) return null;

  const done = project.completed_count + project.failed_count;
  const total = project.page_count + uploads.length;
  const percent = total ? (done / total) * 100 : 0;
  const sending = uploads.filter((item) => item.status !== "failed");
  const current = progress?.current;

  return (
    <section aria-label="Progress" className="mb-6 rounded-xl border border-primary/30 bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1">
        <p className="flex min-w-0 items-center gap-2 font-medium">
          {sending.length ? <CloudUpload className="size-4 shrink-0 text-primary" /> : <Loader2 className="size-4 shrink-0 animate-spin text-primary" />}
          {sending.length ? `Sending ${plural(sending.length, "photo")}` : `${done} of ${plural(total, "page")} finished`}
        </p>
        {left !== undefined && (
          <p className="text-sm text-muted-foreground tabular-nums" aria-live="off">
            {progress!.measured ? "" : "Roughly: "}{eta(left)} left
            {progress!.ahead > 0 && ` · ${plural(progress!.ahead, "page")} from other scans first`}
          </p>
        )}
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100} aria-label="Pages finished">
        <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} />
      </div>
      {current && (
        <p className="mt-3 truncate text-sm text-muted-foreground">
          Page {current.position + 1}: <span className="text-foreground">{STAGE[current.status]}</span> · {current.original_name}
        </p>
      )}
      {progress && (
        <ol className="mt-3 flex flex-wrap gap-1.5">
          {STEPS.map(({ stage, label }) => {
            const count = stage === "ready" ? project.completed_count : progress.by_stage[stage] ?? 0;
            return (
              <li key={stage} className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                                            count ? "border-primary/40 bg-primary/10 text-foreground" : "border-border text-muted-foreground")}>
                {label}<span className="font-medium tabular-nums">{count}</span>
              </li>
            );
          })}
          {project.failed_count > 0 && (
            <li className="flex items-center gap-1.5 rounded-full border border-destructive/40 bg-destructive/10 px-2.5 py-1 text-xs text-destructive">
              <TriangleAlert className="size-3" /> Failed {project.failed_count}
            </li>
          )}
        </ol>
      )}
      {uploads.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <button type="button" className="flex min-h-8 items-center gap-1 text-sm text-muted-foreground hover:text-foreground" aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} /> {plural(uploads.length, "photo")} on the way
          </button>
          {(open || uploads.some((item) => item.status === "failed")) && (
            <ul className="mt-1 max-h-64 divide-y divide-border overflow-y-auto">
              {(open ? uploads : uploads.filter((item) => item.status === "failed")).map((item) => <UploadRow key={item.id} item={item} />)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
