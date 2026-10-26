import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, previewUrl, type Scan } from "../api";
import { about, activeLabel, clock, model, type Model } from "../progress";
import { TERMINAL, cx } from "./ui";

/** Re-render every second while something is moving (for the live timer). */
function useTick(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
}

/** Everything the progress model needs for one file, polled only while it is on its way. */
export function useScanProgress(scanId: string | undefined, scan: Scan | undefined): Model | null {
  const moving = !!scan && (!TERMINAL.has(scan.status) || scan.importStatus === "PENDING");
  const every = moving ? 4_000 : false;
  const enabled = !!scanId && !!scan;
  const findings = useQuery({ queryKey: ["findings", scanId, scan?.status], queryFn: () => api.findings(scanId!), enabled });
  const jobs = useQuery({ queryKey: ["enrichment", scanId], queryFn: () => api.scanEnrichment(scanId!), enabled, refetchInterval: every });
  const paperless = useQuery({ queryKey: ["paperless", scanId], queryFn: () => api.paperless(scanId!), enabled, refetchInterval: every });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, staleTime: 30_000 });
  const skew = useRef(0);
  const progress = useQuery({
    queryKey: ["progress", scanId], enabled, refetchInterval: moving ? 10_000 : false,
    queryFn: async () => {
      const value = await api.progress(scanId!);
      skew.current = Date.now() - Date.parse(value.now);
      return value;
    },
  });
  const result = scan ? model({ scan, findings: findings.data, jobs: jobs.data, paperless: paperless.data, ai: ai.data,
    progress: progress.data, skew: skew.current }) : null;
  useTick(!!result && !result.finished);
  return result;
}

const SEGMENT: Record<string, string> = {
  done: "bg-green-600", active: "bg-sky-500 animate-pulse", waiting: "bg-slate-200 dark:bg-slate-700",
  failed: "bg-red-600", skipped: "bg-slate-200 dark:bg-slate-700 opacity-50",
};

/** Five segments (Checks, Clean copy, AI filing, Saved, Paperless) and one line of what's happening. */
export function ProgressTrack({ value, compact }: { value: Model; compact?: boolean }) {
  const { current } = value;
  return (
    <div data-testid="progress" data-stage={current?.key ?? "done"}>
      <ol className="grid grid-cols-5 gap-1" aria-label="Progress">
        {value.stages.map((stage) => (
          <li key={stage.key} data-stage={stage.key} data-state={stage.state} aria-current={stage.state === "active" ? "step" : undefined}>
            <span className={cx("block h-1.5 rounded-full", SEGMENT[stage.state])} />
            {!compact && <span className={cx("mt-1 block truncate text-xs", stage.state === "active" ? "font-semibold" : "text-slate-500 dark:text-slate-400",
              stage.state === "skipped" && "line-through")}>{stage.label}</span>}
          </li>
        ))}
      </ol>
      {current && (
        <p className={cx("mt-1.5 text-slate-600 dark:text-slate-300", compact ? "text-xs" : "text-sm")} data-testid="progress-line">
          <span className="font-medium">{activeLabel(current)}…</span>
          {current.startedAt ? <> {clock(value.elapsed)}</> : null}
          {!compact && <span className="text-slate-500 dark:text-slate-400"> · usually {about(current.typical).replace("about ", "~")}</span>}
          {value.remaining !== null && <> · <span data-testid="eta">{about(value.remaining)} left</span></>}
          {value.slow && <span className="text-amber-700 dark:text-amber-400"> · taking longer than usual</span>}
          {value.reason && <span className="block text-slate-500 dark:text-slate-400" data-testid="progress-reason">{value.reason}</span>}
        </p>
      )}
    </div>
  );
}

/** A document's first page as a picture; a plain page icon until it exists. */
export function Preview({ documentId, version, size, className, alt = "" }: {
  documentId?: string | null; version?: number; size: "small" | "large"; className?: string; alt?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [documentId, version]);
  if (!documentId || failed) {
    return (
      <span className={cx("flex items-center justify-center rounded-md bg-slate-100 text-slate-400 ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700", className)} aria-hidden>
        <svg width="40%" height="40%" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>
      </span>
    );
  }
  return <img src={previewUrl(documentId, version ?? 1, size)} alt={alt} loading="lazy" onError={() => setFailed(true)} data-testid={`preview-${size}`}
    className={cx("rounded-md bg-white object-cover object-top ring-1 ring-slate-200 dark:ring-slate-700", className)} />;
}
