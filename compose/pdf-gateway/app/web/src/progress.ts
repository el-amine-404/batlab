import type { EnrichmentJob, Findings, PaperlessImport, Progress, Scan } from "./api";

// Where a file is on its way, and how long the rest usually takes here. Five stages a
// person recognises; the estimate comes from this gateway's own recent runs (medians the
// API reports), scaled by page count, plus the files ahead in the queue.

export type StageKey = "check" | "clean" | "filing" | "saved" | "paperless";
export type StageState = "done" | "active" | "waiting" | "failed" | "skipped";
export type Stage = { key: StageKey; label: string; state: StageState; startedAt?: number; typical: number };
export type Model = {
  stages: Stage[];
  current?: Stage;
  /** Seconds since the current stage started. */
  elapsed: number;
  /** Seconds left (null when finished or unknown). */
  remaining: number | null;
  /** The current stage is taking twice as long as usual. */
  slow: boolean;
  /** Why it is waiting, when that is the useful thing to say. */
  reason?: string;
  finished: boolean;
};

export const STAGE_LABEL: Record<StageKey, string> = {
  check: "Checks", clean: "Clean copy", filing: "AI filing", saved: "Saved", paperless: "Paperless",
};
const ACTIVE_LABEL: Record<StageKey, string> = {
  check: "Checking for viruses and hidden content", clean: "Rebuilding a clean copy", filing: "The AI is filing it",
  saved: "Saving to your archive", paperless: "Adding to Paperless",
};
const STOPPED = new Set(["REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"]);
const MOVING_JOB = new Set(["WAITING_FOR_AI", "LEASED", "SUBMITTED"]);
const AI_UP = new Set(["READY", "AVAILABLE", "BUSY"]);

export type Inputs = {
  scan: Scan; findings?: Findings; jobs?: EnrichmentJob[]; paperless?: PaperlessImport;
  ai?: { enabled: boolean; state: string }; progress?: Progress;
  /** Date.now() minus the server's clock, measured when progress was fetched. */
  skew?: number;
};

function typicalOf(progress: Progress | undefined, key: string, fallback: number) {
  const value = progress?.typical[key];
  return typeof value === "number" && value > 0 ? value : fallback;
}

function time(value: string | null | undefined) {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isNaN(parsed) ? undefined : parsed;
}

export function model({ scan, findings, jobs, paperless, ai, progress, skew = 0 }: Inputs): Model {
  const steps = findings?.steps ?? [];
  const first = (type: string) => steps.filter((step) => step.type === type).map((step) => time(step.startedAt)).filter((t): t is number => !!t).sort()[0];
  const last = (type: string) => steps.filter((step) => step.type === type).map((step) => time(step.startedAt)).filter((t): t is number => !!t).sort().at(-1);
  const pages = Math.max(1, progress?.pages ?? 1);
  const perPage = progress?.typical.CDR_PER_PAGE;
  const aiJob = jobs?.find((job) => job.kind === "SUGGEST") ?? jobs?.find((job) => job.kind === "OCR");
  const aiOn = ai?.enabled === true && AI_UP.has(ai.state);
  const wantsPaperless = scan.importStatus !== "NOT_REQUESTED" || (paperless && paperless.status !== "NOT_REQUESTED");

  const typical: Record<StageKey, number> = {
    check: typicalOf(progress, "ANALYSIS", 10) + typicalOf(progress, "SIGNATURE", 5),
    clean: (typeof perPage === "number" && perPage > 0 ? perPage * pages : typicalOf(progress, "CDR", 40)) + typicalOf(progress, "VALIDATION", 8),
    filing: typicalOf(progress, "AI", 60),
    saved: 2 * typicalOf(progress, "ARCHIVE", 4) + typicalOf(progress, "VALIDATION", 8),
    paperless: typicalOf(progress, "PAPERLESS", 30),
  };

  // Which stage the file is in, from its status (coarse) and its jobs.
  const filingNow = !!aiJob && MOVING_JOB.has(aiJob.status) && scan.status !== "READY";
  let position: StageKey | "end";
  switch (scan.status) {
    case "RECEIVED": case "QUARANTINED": case "QUEUED": case "SCANNING": case "ANALYZING": case "AWAITING_POLICY": position = "check"; break;
    case "SANITIZING": position = "clean"; break;
    case "VALIDATING_OUTPUT": position = steps.some((step) => step.type === "ARCHIVE") ? "saved" : "clean"; break;
    case "ARCHIVING": position = filingNow ? "filing" : "saved"; break;
    case "READY": position = wantsPaperless && paperless?.status !== "IMPORTED" && paperless?.status !== "FAILED_FINAL" ? "paperless" : "end"; break;
    default: position = "end";
  }
  const stopped = STOPPED.has(scan.status);
  // Where a stopped file stopped: the last step that failed (or the first that never ran).
  const failedAt: StageKey | undefined = !stopped ? undefined
    : steps.some((step) => step.type === "CDR" && step.status === "FAILED") || steps.some((step) => step.type === "VALIDATION" && step.status === "FAILED") ? "clean"
    : steps.some((step) => step.type === "ARCHIVE" && step.status === "FAILED") ? "saved" : "check";

  const order: StageKey[] = ["check", "clean", "filing", "saved", "paperless"];
  const started: Record<StageKey, number | undefined> = {
    check: first("ANALYSIS"), clean: first("CDR"), filing: time(aiJob?.createdAt), saved: last("ARCHIVE"),
    paperless: scan.status === "READY" ? time(scan.updatedAt) : undefined,
  };
  const stages: Stage[] = order.map((key) => {
    const index = order.indexOf(key);
    let state: StageState;
    if (key === "filing" && (ai?.enabled === false || (!aiJob && scan.status === "READY"))) state = "skipped";
    else if (key === "filing" && aiJob && ["FAILED", "EXPIRED", "SKIPPED"].includes(aiJob.status)) state = "skipped";
    else if (key === "filing" && aiJob && MOVING_JOB.has(aiJob.status) && scan.status === "READY") state = "skipped"; // saved without it; filed later
    else if (key === "paperless" && !wantsPaperless) state = "skipped";
    else if (key === "paperless" && paperless?.status === "FAILED_FINAL") state = "failed";
    else if (stopped) state = key === failedAt ? "failed" : index < order.indexOf(failedAt!) ? "done" : "skipped";
    else if (position === "end") state = "done";
    else if (key === position) state = "active";
    else state = index < order.indexOf(position) ? "done" : "waiting";
    return { key, label: STAGE_LABEL[key], state, startedAt: started[key], typical: typical[key] };
  });

  const current = stages.find((stage) => stage.state === "active");
  const now = Date.now() - skew;
  const elapsed = current?.startedAt ? Math.max(0, (now - current.startedAt) / 1000) : 0;
  const finished = !current;
  let remaining: number | null = null;
  let reason: string | undefined;
  if (current) {
    const queued = current.key === "check" && !current.startedAt;
    const ahead = progress?.ahead ?? 0;
    remaining = Math.max(current.key === "filing" ? 10 : 3, current.typical - elapsed)
      + stages.filter((stage) => stage.state === "waiting").reduce((sum, stage) => sum + stage.typical, 0)
      + (queued ? ahead * typical.clean : 0);
    if (current.key === "filing" && !aiOn) remaining = null;
    if (queued && ahead > 0) reason = `${ahead} file${ahead > 1 ? "s" : ""} ahead of yours`;
    else if (current.key === "filing" && aiJob?.status === "WAITING_FOR_AI")
      reason = aiOn ? "Waiting for the laptop AI to pick it up" : "The laptop AI is away: it waits up to 10 min, then saves without it";
    else if (current.key === "filing") reason = "The laptop AI is reading it";
    else if (current.key === "paperless" && paperless?.status === "FAILED_RETRYABLE") reason = "Paperless didn't answer; trying again";
  }
  return { stages, current, elapsed, remaining, slow: !!current && elapsed > 2 * current.typical + 10, reason, finished };
}

export function activeLabel(stage: Stage) {
  return ACTIVE_LABEL[stage.key];
}

/** "0:42", "3:05". */
export function clock(seconds: number) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** "under a minute", "about 3 min". */
export function about(seconds: number) {
  if (seconds < 50) return "under a minute";
  return `about ${Math.max(1, Math.round(seconds / 60))} min`;
}
