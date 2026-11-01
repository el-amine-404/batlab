import type { Findings, Scan } from "./api";
import { formatBytes, formatDuration, type StepState, type Tone } from "./components/ui";

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
export function verdict(scan: Scan, findings?: Findings): { tone: Tone; title: string; text: string; danger?: string; next?: string } {
  const reason = holdReason(scan, findings);
  if (reason) return { tone: scan.status === "REVIEW_REQUIRED" ? "warn" : "bad", ...reason };
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
/** The engines, by what they look for. */
export const ENGINE: Record<string, { name: string; what: string }> = {
  clamav: { name: "Virus signatures", what: "ClamAV compares the file with known malware." },
  qpdf: { name: "PDF structure", what: "qpdf checks that the file is a well-formed PDF." },
  "qpdf-encryption": { name: "Password protection", what: "A locked file can't be inspected." },
  pdfid: { name: "Risky PDF features", what: "PDFiD counts scripts, auto-open actions and attachments." },
  "pdf-parser": { name: "Object inventory", what: "pdf-parser lists every object in the file." },
  "pdf-keywords": { name: "Hidden actions", what: "Looks for names like JavaScript and Launch." },
  yara: { name: "Malware patterns", what: "YARA rules for known attack techniques." },
  "qpdf-json": { name: "Only what the rebuild writes", what: "Reads the decoded file: no scripts, links, forms or visible fonts, and pages draw from a fixed list." },
  "page-count": { name: "Page count", what: "The copy has as many pages as the original." },
  pdftoppm: { name: "Pages can be drawn", what: "Draws each page to catch blank or broken output." },
};

export const CHECK_STATUS: Record<string, { text: string; tone: Tone }> = {
  OK: { text: "Passed", tone: "good" }, CLEAN: { text: "Clean", tone: "good" }, CLEAR: { text: "Not locked", tone: "good" },
  TIMEOUT: { text: "Ran out of time", tone: "warn" }, FAILED: { text: "Failed to run", tone: "warn" }, UNAVAILABLE: { text: "Unavailable", tone: "warn" },
  STALE: { text: "Signatures out of date", tone: "warn" }, ERROR: { text: "No clear answer", tone: "warn" },
  ENCRYPTED: { text: "Locked", tone: "warn" }, FOUND: { text: "Malware found", tone: "bad" },
};

export const STAGE_NAME: Record<string, string> = { ANALYSIS: "First scan", SIGNATURE: "Signature check", CDR: "Rebuild", VALIDATION: "Final check", ARCHIVE: "Saved to the archive" };

/** What each step is for, shown when it is opened. */
export const STAGE_ABOUT: Record<string, string> = {
  received: "The upload is stored unopened in quarantine. Only the sealed workers read it, and it never reaches your browser.",
  scan: "Looks at the original for known malware, hidden scripts and a broken structure. A clean result doesn't release the file: only the rebuilt copy can leave.",
  signature: "A digital signature can't survive a rebuild, so it's verified on the original and recorded here.",
  rebuild: "The original is opened in a sandbox with no network, and all that comes out is a list of shapes and pixels. A separate writer makes a new PDF from that list, so fonts, scripts, links, forms and attachments have nowhere to go. A page it can't express that way becomes pixels.",
  recheck: "The rebuilt copy is new bytes, so it goes through the checks again, and it may contain only plain shapes, images and invisible text. Every check has to finish and pass: one without an answer holds the file.",
  archive: "Files the clean copy with its details. The original stays in quarantine.",
};

/** Time spent in a stage, every try included. */
export function stageDuration(key: string, findings: Findings | undefined): string | null {
  const type = { scan: "ANALYSIS", signature: "SIGNATURE", rebuild: "CDR", recheck: "VALIDATION", archive: "ARCHIVE" }[key];
  const runs = findings?.steps.filter((step) => step.type === type && step.startedAt && step.finishedAt) ?? [];
  if (!runs.length) return null;
  return formatDuration(runs.reduce((sum, step) => sum + new Date(step.finishedAt!).getTime() - new Date(step.startedAt!).getTime(), 0));
}

const CODE_TEXT: Record<string, string> = {
  CLAMAV_UNAVAILABLE: "The virus scanner couldn't be reached.", CLAMAV_DATABASE_STALE: "The virus signatures are out of date.",
  CLAMAV_SCAN_ERROR: "The virus scanner gave no clear answer.", STRUCTURE_UNREADABLE: "The rebuilt copy's contents couldn't be read.",
  PAGE_COUNT_UNKNOWN: "The number of pages couldn't be worked out.", PAGE_COUNT_MISMATCH: "The rebuilt copy has a different number of pages from the original.",
  RENDER_FAILED: "The rebuilt copy's pages couldn't be drawn.", RENDER_PAGE_COUNT: "Not every page of the rebuilt copy could be drawn.",
  OUTPUT_ALL_PAGES_BLANK: "Every page of the rebuilt copy came out blank.", OUTPUT_HASH_MISMATCH: "The rebuilt copy's bytes changed after it was made.",
  OUTPUT_UNREADABLE: "The rebuilt copy couldn't be read from storage.", VALIDATOR_NO_REPORT: "The final check stopped without a report.",
  FORBIDDEN_STRUCTURE: "The rebuilt copy contains something the rebuild never writes.", NON_IMAGE_XOBJECT: "The rebuilt copy contains something the rebuild never writes.",
  PAGE_RESOURCES_UNEXPECTED: "The rebuilt copy contains something the rebuild never writes.", CONTENT_NOT_ALLOWED: "A page draws with something outside the allowed list.",
  IMAGE_NOT_ALLOWED: "An image isn't in the plain form the rebuild writes.", GRAPHICS_STATE_NOT_ALLOWED: "A page sets more than opacity.", CONTENT_UNREADABLE: "A page's content couldn't be read.", FONT_NOT_ALLOWED: "The rebuilt copy contains an unexpected font.",
  VISIBLE_TEXT: "The rebuilt copy has visible text where only images belong.", MALWARE_DETECTED: "The virus scanner recognised malware.",
  YARA_MATCH: "A malware pattern matched.", PDF_ENCRYPTED: "The file is locked with a password, so it can't be inspected.",
  PDF_KEYWORD_EMBEDDEDFILE: "The file has an attachment inside it.", PDF_STRUCTURE_INVALID: "The file isn't a well-formed PDF.",
  ENCRYPTION_CHECK_FAILED: "Couldn't tell whether the file is locked.", ENGINE_FAILED: "A required check didn't finish.",
};

const names = (items: string[]) => items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
const FALSE_ALARM = "Probably not: no check reported anything dangerous. The file is held because a check gave no answer, and no answer never counts as a pass.";
const AGAIN = "Use Delete on this page, then upload it again. Your own file isn't touched.";

/** Why a file didn't come through, from the last run's own evidence. */
export function holdReason(scan: Scan, f: Findings | undefined): { title: string; text: string; danger?: string; next?: string } | null {
  if (!["REVIEW_REQUIRED", "BLOCKED", "FAILED"].includes(scan.status) || !f) return null;
  const last = f.validation.at(-1);
  const failed = last && last.verdict !== "PASS" ? last : undefined;
  const size = failed?.outputBytes != null ? ` (${formatBytes(failed.outputBytes)})` : "";
  if (failed?.failureCodes.includes("OUTPUT_TOO_LARGE")) {
    return { title: "Held: the rebuilt copy is too large", text: `Rebuilding made the file${size} larger than the 60 MB the final checks accept, so they didn't run.`,
      danger: "Unknown: the rebuilt copy wasn't checked.", next: "Split the PDF into smaller parts and upload those." };
  }
  const late = failed?.checks?.filter((check) => check.status === "TIMEOUT") ?? [];
  if (late.length) {
    const limit = late[0].limitSeconds ? ` the ${late[0].limitSeconds} s it's allowed` : " its time limit";
    return { title: "Held: a final check ran out of time",
      text: `${names(late.map((check) => ENGINE[check.engine ?? ""]?.name ?? check.engine ?? "A check"))} didn't finish within${limit} on the rebuilt copy${size}.`,
      danger: FALSE_ALARM, next: `${AGAIN} It can go through when the server is less busy.` };
  }
  const known = (codes: string[]) => [...new Set(codes.map((code) => CODE_TEXT[code]).filter(Boolean))];
  if (failed) {
    const said = known(failed.failureCodes);
    const inconclusive = failed.verdict === "UNKNOWN";
    return { title: inconclusive ? "Held: a final check couldn't finish" : "Held: the rebuilt copy failed its final check",
      text: `${said.join(" ") || "The rebuilt copy didn't pass."} Tried ${f.validation.length} ${f.validation.length === 1 ? "time" : "times"}.`,
      danger: inconclusive ? FALSE_ALARM : "Possibly: the copy isn't what a clean rebuild should produce, so it's kept away from your archive.",
      next: inconclusive ? AGAIN : "Only keep going if you trust where the file came from." };
  }
  const cdr = f.cdr.at(-1);
  if (cdr && cdr.status !== "SUCCEEDED") {
    return { title: "Held: the file couldn't be rebuilt", text: `The sandbox couldn't turn its pages into a clean copy${cdr.failureCode ? ` (${cdr.failureCode})` : ""}.`,
      danger: "Not necessarily: damaged or very unusual PDFs do this too.", next: "Try exporting the PDF again from its source, then upload that." };
  }
  const notes = f.analysis?.findings.filter((item) => item.severity !== "INFO") ?? [];
  if (notes.length) {
    const blocked = scan.status === "BLOCKED";
    const said = known(notes.flatMap((item) => item.code ? [item.code] : []));
    return { title: blocked ? "Blocked: this file looks dangerous" : "Held: the first scan raised a concern",
      text: said.join(" ") || (notes[0].message ?? "A check raised a concern."),
      danger: blocked ? "Yes, treat it as dangerous. It stays locked away and is never released." : undefined,
      next: blocked ? "Don't open your own copy of this file." : AGAIN };
  }
  return null;
}

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
  // The first scan's own result: the scan's overall one may come from a later step.
  const detection = findings?.analysis?.detection ?? scan.detection;
  const signature = findings?.signature;
  const passes = findings?.validation.filter((item) => item.verdict === "PASS").length ?? 0;
  return [
    { key: "received", label: "Received and locked away", state: "done",
      detail: "Stored unopened in a sealed area." },
    { key: "scan", label: "Checked for viruses and hidden content", state: state("ANALYSIS"),
      detail: state("ANALYSIS") !== "done" ? "ClamAV, YARA and PDF structure checks." :
        detection === "NO_THREATS_DETECTED" || detection === "CLEAN" ? `Nothing dangerous found${notes ? ` (${notes} technical note${notes > 1 ? "s" : ""} in the details)` : ""}.` :
        detection === "MALICIOUS" ? "A threat was found." : detection === "SUSPICIOUS" ? "Something suspicious was found." : "The result was not conclusive." },
    { key: "signature", label: "Checked for a digital signature", state: state("SIGNATURE"),
      detail: !signature ? undefined : signature.state === "UNSIGNED" ? "Not digitally signed." :
        `Signed (${signature.signatureCount}). The signed original is kept as evidence; the clean copy can't carry the signature.` },
    { key: "rebuild", label: "Rebuilt as a clean copy", state: state("CDR"),
      detail: rebuildNote(findings) },
    { key: "recheck", label: "Checked the clean copy again", state: state("VALIDATION"),
      detail: passes ? "Passed." : state("VALIDATION") === "failed" ? recheckNote(findings) : undefined },
    { key: "archive", label: "Saved to your archive", state: state("ARCHIVE") },
  ];
}

function rebuildNote(findings: Findings | undefined) {
  const last = findings?.cdr.filter((item) => item.status === "SUCCEEDED").at(-1);
  if (last?.rebuildMode !== "SHAPES") return last?.shapesFailure ? "Every page redrawn as an image: the shapes rebuild couldn't handle this file." : "Every page redrawn as an image in a sandbox.";
  const pixels = last.shapes?.pixelPages ?? 0;
  return `Pages rewritten as plain shapes and images in a sandbox${pixels ? `; ${pixels} of ${last.shapes?.pages} as pixels` : ""}.`;
}

function recheckNote(findings: Findings | undefined) {
  const last = findings?.validation.at(-1);
  const bad = last?.checks?.filter((check) => !["OK", "CLEAN", "CLEAR"].includes(check.status ?? "")).length;
  return bad ? `${bad} of ${last!.checks!.length} checks had no passing answer.` : "It didn't pass. Open for details.";
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
