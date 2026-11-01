import { ChevronDown, ClipboardCopy, Wrench } from "lucide-react";
import { Collapsible } from "radix-ui";
import type { ReactNode } from "react";
import { toast } from "sonner";
import type { Findings, Scan } from "../api";
import { CHECK_STATUS, ENGINE, STAGE_ABOUT, stageDuration } from "../words";
import { Badge, Button, CopyButton, Details, Muted, StepMark, cx, formatBytes, formatDate, formatDuration, toClipboard, type StepState } from "./ui";

export type PipelineStep = { key: string; label: ReactNode; detail?: ReactNode; state: StepState };

type CheckRow = { engine?: string | null; status?: string | null; durationMs?: number | null; limitSeconds?: number | null; version?: string | null };

/** The checks of one run: what each looks for, how it ended, how long it took. */
function CheckList({ checks }: { checks: CheckRow[] }) {
  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-background/40">
      {checks.map((check, index) => {
        const info = ENGINE[check.engine ?? ""];
        const status = CHECK_STATUS[check.status ?? ""] ?? { text: check.status ?? "No result", tone: "muted" as const };
        return (
          <li key={`${check.engine}-${index}`} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2.5" data-check={check.engine} data-check-status={check.status}>
            <div className="min-w-0 flex-1 basis-48">
              <p className="text-sm font-medium">{info?.name ?? check.engine ?? "Check"}</p>
              {info && <p className="text-xs text-muted-foreground">{info.what}</p>}
            </div>
            <div className="flex items-center gap-2">
              {check.durationMs != null && <span className="text-xs text-muted-foreground tabular-nums">{formatDuration(check.durationMs)}{check.limitSeconds ? ` of ${check.limitSeconds} s allowed` : ""}</span>}
              <Badge tone={status.tone}>{status.text}</Badge>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function stageChecks(key: string, findings: Findings | undefined): CheckRow[] {
  if (key === "scan") return findings?.analysis?.engines ?? [];
  if (key === "recheck") return findings?.validation.at(-1)?.checks ?? [];
  return [];
}

/** The steps a file goes through. Each opens onto what it does and how each check ended; one that failed starts open. */
export function Pipeline({ items, findings }: { items: PipelineStep[]; findings: Findings | undefined }) {
  return (
    <ol data-testid="steps">
      {items.map((item, index) => {
        const about = STAGE_ABOUT[item.key];
        const checks = stageChecks(item.key, findings);
        const duration = stageDuration(item.key, findings);
        const open = !!about || checks.length > 0;
        const dim = item.state === "waiting" || item.state === "skipped";
        const head = (
          <>
            <span className="relative shrink-0"><StepMark state={item.state} /></span>
            <span className="min-w-0 flex-1 pt-0.5">
              <span className={cx("block text-sm font-medium", dim && "text-muted-foreground")}>{item.label}</span>
              {item.detail && <span className="mt-0.5 block text-sm text-muted-foreground">{item.detail}</span>}
            </span>
            {duration && <span className="shrink-0 pt-1 text-xs text-muted-foreground tabular-nums">{duration}</span>}
          </>
        );
        return (
          <li key={item.key} className="relative" data-step={item.key} data-state={item.state}>
            {index < items.length - 1 && <span aria-hidden className={cx("absolute top-11 bottom-0 left-[1.625rem] w-px", item.state === "done" ? "bg-success/40" : "bg-border")} />}
            {open ? (
              <Collapsible.Root key={item.state} defaultOpen={item.state === "failed"} className="group/step">
                <Collapsible.Trigger className="flex min-h-11 w-full items-start gap-3 rounded-lg p-3 text-left transition hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                  {head}
                  <ChevronDown aria-hidden className="mt-1.5 size-4 shrink-0 text-muted-foreground transition group-data-[state=open]/step:rotate-180" />
                </Collapsible.Trigger>
                <Collapsible.Content className="space-y-3 pr-3 pb-4 pl-[3.25rem]">
                  {about && <p className="text-sm text-muted-foreground">{about}</p>}
                  {checks.length > 0 && <CheckList checks={checks} />}
                </Collapsible.Content>
              </Collapsible.Root>
            ) : <div className="flex items-start gap-3 p-3">{head}</div>}
          </li>
        );
      })}
    </ol>
  );
}

function Fact({ label, children, copy }: { label: string; children: ReactNode; copy?: string }) {
  return (
    <div className="flex items-start justify-between gap-2 py-2.5">
      <div className="min-w-0">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="mt-0.5 text-sm break-words">{children}</dd>
      </div>
      {copy && <CopyButton text={copy} label={label} className="-mt-0.5" />}
    </div>
  );
}

const logLine = (step: Findings["steps"][number]) => [
  step.startedAt ? new Date(step.startedAt).toISOString() : "-", step.type, `try ${step.attempt}`, step.status,
  stepMs(step) != null ? `${stepMs(step)} ms` : "", step.errorCode ?? "",
].filter(Boolean).join("  ");

function stepMs(step: Findings["steps"][number]) {
  return step.startedAt && step.finishedAt ? new Date(step.finishedAt).getTime() - new Date(step.startedAt).getTime() : null;
}

const PASSED = new Set(["OK", "CLEAN", "CLEAR"]);

/** The essentials in plain words; each check's own result is in the pipeline, the rest in the copied log. */
export function TechnicalDetails({ scan, findings, sidecar, documentId, doc }: {
  scan: Scan; findings?: Findings; sidecar?: { metadataRevision: number; organization: { reason?: string }; textExtraction?: { engine?: string | null; method?: string | null } | null };
  documentId: string | null; doc?: { sha256: string; publishedAt: string; bytes?: number };
}) {
  const f = findings;
  const rebuild = f?.cdr.filter((item) => item.status === "SUCCEEDED").at(-1);
  const last = f?.validation.at(-1);
  const checks = last?.checks ?? [];
  const failing = checks.filter((check) => !PASSED.has(check.status ?? ""));
  const size = doc?.bytes ?? rebuild?.outputBytes;
  const copyLog = async () => {
    const text = [`${scan.scanId}  ${scan.status}  ${scan.detection}  ${scan.policy}  source ${scan.sourceSha256}`,
      ...(documentId ? [`${documentId}  ${doc?.sha256 ?? ""}`] : []),
      ...(f?.steps ?? []).map(logLine),
      ...(f?.cdr ?? []).map((item) => `rebuild try ${item.attempt}: ${item.status} ${item.rebuildMode ?? ""} ${item.outputBytes ?? ""} bytes ${item.failureCode ?? item.shapesFailure ?? ""}`),
      ...(f?.analysis?.engines ?? []).map((engine) => `first scan ${engine.engine} ${engine.version ?? ""}: ${engine.status} ${engine.durationMs ?? ""} ms`),
      ...(f?.analysis?.findings ?? []).map((one) => `first scan: ${one.severity} ${one.code} ${one.engine}: ${one.message}`),
      ...(f?.validation ?? []).flatMap((item) => [`final check ${item.attempt}: ${item.verdict} ${item.failureCodes.join(",")} ${item.outputBytes ?? ""} bytes`,
        ...(item.checks ?? []).map((check) => `  ${check.engine}: ${check.status} ${check.durationMs ?? ""} ms`),
        ...(item.findings ?? []).map((one) => `  ${one.code} ${one.engine}: ${one.message}`)])].join("\n");
    try { await toClipboard(text); toast.success("Log copied."); } catch { toast.error("Couldn't copy the log."); }
  };
  return (
    <Details summary="Technical details" icon={<Wrench className="size-4" />}>
      <dl className="divide-y divide-border" data-testid="technical">
        {rebuild && (
          <Fact label="Clean copy">
            {rebuild.rebuildMode === "SHAPES" ? "Rebuilt as sharp shapes and images" : "Rebuilt as images"}{size != null && `, ${formatBytes(size)}`} (original {formatBytes(scan.sourceBytes)})
          </Fact>
        )}
        {last && (
          <Fact label="Final checks">
            <span data-testid="checks">{last.verdict === "PASS" ? `All ${checks.length || ""} passed`.replace("  ", " ")
              : failing.length ? `${failing.length} of ${checks.length} had no passing answer: ${failing.map((check) => ENGINE[check.engine ?? ""]?.name ?? check.engine).join(", ")}`
              : "Didn't pass"}</span>
          </Fact>
        )}
        {sidecar?.textExtraction?.engine && <Fact label="Text read by">{sidecar.textExtraction.engine}</Fact>}
        {sidecar?.organization.reason && <Fact label="Filed because">{sidecar.organization.reason}</Fact>}
        <Fact label="Uploaded">{formatDate(scan.createdAt)}</Fact>
        {doc && <Fact label="Saved to the archive">{formatDate(doc.publishedAt)}</Fact>}
        <Fact label="Fingerprint of the original" copy={scan.sourceSha256}><code className="text-xs break-all">{scan.sourceSha256}</code></Fact>
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="secondary" onClick={() => void copyLog()} data-testid="copy-log"><ClipboardCopy /> Copy full log</Button>
        {documentId && <a className="text-xs text-primary underline-offset-4 hover:underline" href={`/api/v1/archive/${encodeURIComponent(documentId)}/sidecar`} download>Download the metadata file</a>}
      </div>
      <Muted className="mt-3 text-xs">No scanner can prove a PDF is safe. Handwritten or drawn signatures aren't verified.</Muted>
    </Details>
  );
}
