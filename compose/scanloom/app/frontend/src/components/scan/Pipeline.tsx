import { Check, CheckCircle2, ChevronDown, Clock, CloudUpload, FileImage, Image as ImageIcon, Loader2, type LucideIcon, RotateCcw, ScanSearch, ShieldCheck, TriangleAlert, Wand2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { Project } from "@/lib/api";
import { cancelUpload, retryUpload, type Upload, useUploads } from "@/lib/uploads";
import { cn, eta, formatBytes, plural } from "@/lib/utils";

import { STAGE, STEPS } from "./labels";

/** Ticks once a second while it matters: what is left counts down between the server's answers. */
function useCountdown(seconds: number | undefined, measuredAt: number) {
  const [now, setNow] = useState(Date.now());
  const counting = seconds !== undefined;
  useEffect(() => {
    if (!counting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [counting]);
  if (seconds === undefined) return undefined;
  return Math.max(1, seconds - Math.max(0, (now - measuredAt) / 1000));
}

const capital = (text: string) => `${text[0].toUpperCase()}${text.slice(1)}`;

const ICONS: Record<string, LucideIcon> = { queued: Clock, scanning: ShieldCheck, decoding: ImageIcon, detecting: ScanSearch, rendering: Wand2, ready: CheckCircle2 };

/** The road of the page being worked on: steps it has passed, the one it is at, those to come. How many are waiting is said beside it. */
function Steps({ project }: { project: Project }) {
  const progress = project.progress!;
  const steps = STEPS.filter(({ stage }) => stage !== "queued");
  const at = steps.findIndex(({ stage }) => stage === progress.current?.status);
  const stateOf = (index: number) => (at < 0 ? "later" : index < at ? "done" : index === at ? "now" : "later");
  const waiting = progress.by_stage.queued ?? 0;
  return (
    <>
      <ol className="mt-4 flex items-start" aria-label="Steps of this page">
        {steps.map(({ stage, label }, index) => {
          const Icon = ICONS[stage];
          const state = stateOf(index);
          return (
            <li key={stage} className="relative flex flex-1 flex-col items-center gap-1.5 text-center" aria-current={state === "now" ? "step" : undefined}>
              {index > 0 && <span aria-hidden className={cn("absolute top-[1.125rem] right-1/2 left-[-50%] h-0.5 -translate-y-1/2 rounded", state === "later" ? "bg-border" : "bg-primary")} />}
              <span className={cn("relative z-10 flex size-9 items-center justify-center rounded-full border-2 bg-card transition-colors",
                                  state === "now" && "border-primary text-primary shadow-[0_0_0_4px_color-mix(in_oklch,var(--primary)_18%,transparent)]",
                                  state === "done" && "border-primary bg-primary text-primary-foreground",
                                  state === "later" && "border-border text-muted-foreground")}>
                {state === "done" ? <Check className="size-4" /> : <Icon className={cn("size-4", state === "now" && "animate-pulse")} />}
              </span>
              <span className={cn("text-xs", state === "later" ? "text-muted-foreground" : "font-medium text-foreground")}>{label}</span>
            </li>
          );
        })}
      </ol>
      {waiting > 0 && <p className="mt-3 flex items-center justify-center gap-1.5 text-xs text-muted-foreground"><Clock className="size-3.5" /> {plural(waiting, "more page")} waiting {waiting === 1 ? "its" : "their"} turn</p>}
    </>
  );
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
  const uploads = useUploads(project.id);
  const progress = project.progress;
  // Photos still on their way count too: they will be pages to make, once there.
  const waiting = uploads.filter((item) => item.status !== "failed").length;
  const known = progress?.eta_seconds || (project.held_count === 0 && waiting) ? (progress?.eta_seconds ?? 0) + (project.held_count === 0 ? waiting * project.page_seconds : 0) : undefined;
  const left = useCountdown(known, measuredAt);
  const [open, setOpen] = useState(false);
  if (!progress?.eta_seconds && !uploads.length && !project.held_count) return null;
  if (project.held_count && !uploads.length) return null;

  const done = project.completed_count + project.failed_count;
  const total = project.page_count + uploads.length;
  const percent = total ? (done / total) * 100 : 0;
  const sending = uploads.filter((item) => item.status !== "failed");
  const current = progress?.current;

  return (
    <section aria-label="Progress" className="mb-6 rounded-xl border border-primary/30 bg-card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="flex min-w-0 items-center gap-2 text-lg font-semibold">
          {sending.length ? <CloudUpload className="size-5 shrink-0 self-center text-primary" /> : <Loader2 className="size-5 shrink-0 animate-spin self-center text-primary" />}
          {(progress?.redoing ?? 0) > 0 && !sending.length ? `Updating ${plural(progress!.redoing, "page")}: ${eta(left ?? 1)} left` : left !== undefined ? `${capital(eta(left))} left` : "Checking your photos"}
        </p>
        <p className="text-sm text-muted-foreground tabular-nums">
          {sending.length ? `Sending ${plural(sending.length, "photo")} · ` : ""}{done} of {plural(total, "page")} finished
          {progress && !progress.measured ? " · first estimate" : ""}
          {(progress?.ahead ?? 0) > 0 && ` · ${plural(progress!.ahead, "page")} from other scans first`}
        </p>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100} aria-label="Pages finished">
        <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} />
      </div>
      {current && (
        <p className="mt-3 truncate text-sm text-muted-foreground">
          Page {current.position + 1}: <span className="text-foreground">{STAGE[current.status]}</span> · {current.original_name}
        </p>
      )}
      {progress && <Steps project={project} />}
      {project.failed_count > 0 && (
        <p className="mt-2 flex items-center gap-1.5 text-sm text-destructive"><TriangleAlert className="size-4" /> {plural(project.failed_count, "page")} failed</p>
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
