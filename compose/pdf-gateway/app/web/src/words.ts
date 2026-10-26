import type { Findings, Scan } from "./api";
import type { StepState, Tone } from "./components/ui";

// What the gateway's states mean, in the words a person uses. One place, so
// the list, the document page and the upload page say the same thing.

/** A short status for lists: "Checking", "Ready", "Held"... */
export function statusWord(status: Scan["status"]): { text: string; tone: Tone } {
  switch (status) {
    case "READY": return { text: "Ready", tone: "good" };
    case "REVIEW_REQUIRED": return { text: "Held", tone: "warn" };
    case "BLOCKED": return { text: "Blocked", tone: "bad" };
    case "FAILED": return { text: "Couldn't process", tone: "bad" };
    case "CANCELLED": return { text: "Deleted", tone: "muted" };
    default: return { text: "Checking…", tone: "info" };
  }
}

/** The one-line verdict at the top of a document's page. */
export function verdict(scan: Scan): { tone: Tone; title: string; text: string } {
  switch (scan.status) {
    case "READY":
      return { tone: "good", title: "Safe copy ready",
        text: "Nothing dangerous was found, and you get a clean, rebuilt copy of the file." };
    case "REVIEW_REQUIRED":
      return { tone: "warn", title: "Held: not released",
        text: "A check could not be completed or found something unusual, so the file is kept aside and no copy was made. Nothing from it reached your archive or Paperless." };
    case "BLOCKED":
      return { tone: "bad", title: "Blocked: this file looks dangerous",
        text: "The virus or content checks flagged it. It stays locked away and is never released." };
    case "FAILED":
      return { tone: "bad", title: "Couldn't process this file",
        text: "Something went wrong while checking it, so nothing was released. Upload it again later, or open the details below." };
    case "CANCELLED":
      return { tone: "muted", title: "Deleted", text: "The held original was deleted." };
    default:
      return { tone: "info", title: "Checking your file…", text: "This usually takes under a minute. You can leave this page; it carries on." };
  }
}

type Step = { key: string; label: string; detail?: string; state: StepState };
const STAGE_ORDER = ["ANALYSIS", "SIGNATURE", "CDR", "VALIDATION", "ARCHIVE"] as const;

/** The safety steps, in order, with what each one found. */
export function safetySteps(scan: Scan, findings: Findings | undefined): Step[] {
  const latest = new Map<string, string>();
  for (const step of findings?.steps ?? []) latest.set(step.type, step.status);
  // The evidence itself also counts: a verification, a successful rebuild, a passing final check.
  if (findings?.signature) latest.set("SIGNATURE", "SUCCEEDED");
  if (findings?.cdr.some((item) => item.status === "SUCCEEDED")) latest.set("CDR", "SUCCEEDED");
  if (findings?.validation.some((item) => item.verdict === "PASS")) latest.set("VALIDATION", "SUCCEEDED");
  const terminal = ["READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"].includes(scan.status);
  const running = STAGE_ORDER.find((stage) => latest.get(stage) === "RUNNING")
    ?? (terminal ? undefined : STAGE_ORDER.find((stage) => latest.get(stage) !== "SUCCEEDED"));
  const state = (stage: (typeof STAGE_ORDER)[number]): StepState => {
    const status = latest.get(stage);
    if (status === "SUCCEEDED") return "done";
    if (status === "FAILED" && terminal) return "failed";
    if (stage === running) return "running";
    return terminal ? "skipped" : "waiting";
  };
  const notes = findings?.analysis?.findings.length ?? 0;
  const detection = scan.detection;
  const signature = findings?.signature;
  const passes = findings?.validation.filter((item) => item.verdict === "PASS").length ?? 0;
  return [
    { key: "received", label: "Received and locked away", state: "done",
      detail: "Your original goes into a sealed area; it is never opened in your browser." },
    { key: "scan", label: "Checked for viruses and hidden content", state: state("ANALYSIS"),
      detail: state("ANALYSIS") !== "done" ? "ClamAV, YARA and PDF structure checks." :
        detection === "NO_THREATS_DETECTED" ? `Nothing dangerous found${notes ? ` (${notes} technical note${notes > 1 ? "s" : ""} in the details)` : ""}.` :
        detection === "MALICIOUS" ? "A threat was found." : detection === "SUSPICIOUS" ? "Something suspicious was found." : "The result was not conclusive." },
    { key: "signature", label: "Checked for a digital signature", state: state("SIGNATURE"),
      detail: !signature ? undefined : signature.state === "UNSIGNED" ? "Not digitally signed." :
        `Signed (${signature.signatureCount}). The signed original is kept as evidence; the clean copy can't carry the signature.` },
    { key: "rebuild", label: "Rebuilt as a clean copy", state: state("CDR"),
      detail: "Every page is redrawn in a sealed sandbox, so scripts, links, forms and attachments can't come along." },
    { key: "recheck", label: "Checked the clean copy again", state: state("VALIDATION"),
      detail: passes ? "Passed." : undefined },
    { key: "archive", label: "Saved to your archive", state: state("ARCHIVE") },
  ];
}

export const AI_STATE: Record<string, string> = {
  DISABLED: "Not set up", UNKNOWN: "Not connected yet", OFFLINE: "The AI laptop is off or asleep",
  SERVICE_DOWN: "LM Studio isn't running on the laptop", MODEL_MISSING: "The AI model isn't installed",
  AVAILABLE: "Connected", READY: "Ready", BUSY: "Busy", DEGRADED: "Paused after bad answers",
};

export const FIELD_LABEL: Record<string, string> = { title: "Title", issuer: "From", documentType: "Type", documentDate: "Date" };

export const TYPE_LABEL: Record<string, string> = {
  invoice: "Invoice", receipt: "Receipt", bank_statement: "Bank statement", payslip: "Payslip", contract: "Contract",
  tax_document: "Tax document", insurance: "Insurance", medical: "Medical", identity_document: "Identity document",
  certificate: "Certificate", letter: "Letter", school_document: "School document", presentation: "Presentation",
  report: "Report", manual: "Manual", other: "Document",
};
