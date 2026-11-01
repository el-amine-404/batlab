import { ChevronDown, ClipboardCopy, Fingerprint, ListChecks, ScanSearch, ScrollText } from "lucide-react";
import { Collapsible, Tabs } from "radix-ui";
import type { ReactNode } from "react";
import { toast } from "sonner";
import type { Findings, Scan } from "../api";
import { CHECK_STATUS, ENGINE, STAGE_ABOUT, STAGE_NAME, stageDuration } from "../words";
import { Badge, Button, CopyButton, Muted, StepMark, cx, formatBytes, formatDate, formatDuration, toClipboard, type StepState } from "./ui";

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

const TAB = "flex h-9 shrink-0 items-center gap-1.5 rounded-md px-3 text-sm text-muted-foreground transition outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:bg-accent data-[state=active]:font-medium data-[state=active]:text-foreground [&_svg]:size-4";

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

/** For someone looking into it: every run of every check, the log, and the identifiers. */
export function TechnicalDetails({ scan, findings, sidecar, documentId, doc }: {
  scan: Scan; findings?: Findings; sidecar?: { metadataRevision: number; organization: { reason?: string } };
  documentId: string | null; doc?: { sha256: string; publishedAt: string };
}) {
  const f = findings;
  const copyLog = async () => {
    const text = [`${scan.scanId}  ${scan.status}  ${scan.detection}  ${scan.policy}`, ...(f?.steps ?? []).map(logLine),
      ...(f?.validation ?? []).flatMap((item) => (item.findings ?? []).map((one) => `final check ${item.attempt}: ${one.code} ${one.engine}: ${one.message}`))].join("\n");
    try { await toClipboard(text); toast.success("Log copied."); } catch { toast.error("Couldn't copy the log."); }
  };
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card shadow-xl shadow-black/20 backdrop-blur-xl" data-testid="technical">
      <h2 className="px-5 pt-5 text-base font-semibold tracking-tight sm:px-6">Technical details</h2>
      <Tabs.Root defaultValue="checks">
        <Tabs.List aria-label="Technical details" className="mx-5 mt-3 flex gap-1 overflow-x-auto rounded-lg border border-border bg-background/40 p-0.5 sm:mx-6">
          <Tabs.Trigger className={TAB} value="checks"><ListChecks /> Runs</Tabs.Trigger>
          <Tabs.Trigger className={TAB} value="scan"><ScanSearch /> First scan</Tabs.Trigger>
          <Tabs.Trigger className={TAB} value="log"><ScrollText /> Log</Tabs.Trigger>
          <Tabs.Trigger className={TAB} value="ids"><Fingerprint /> Identifiers</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="checks" className="space-y-3 px-5 py-4 outline-none sm:px-6" data-testid="checks">
          {!f?.cdr.length && !f?.validation.length && <Muted>The file hasn't reached the rebuild yet.</Muted>}
          {f?.cdr.map((item) => (
            <div key={`c${item.attempt}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-background/40 px-3 py-2.5">
              <span className="text-sm font-medium">Rebuild, try {item.attempt}</span>
              <span className="flex items-center gap-2">
                {item.durationMs != null && <span className="text-xs text-muted-foreground tabular-nums">{formatDuration(item.durationMs)}</span>}
                {item.failureCode && <code className="text-xs break-all">{item.failureCode}</code>}
                <Badge tone={item.status === "SUCCEEDED" ? "good" : "warn"}>{item.status}</Badge>
              </span>
            </div>
          ))}
          {f?.validation.map((item) => (
            <div key={`v${item.attempt}`} className="space-y-2 rounded-lg border border-border bg-background/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">Final check {item.attempt}{item.artifact === "EMBEDDED_METADATA" ? " (with metadata)" : ""}</span>
                <Badge tone={item.verdict === "PASS" ? "good" : item.verdict === "FAIL" ? "bad" : "warn"}>{item.verdict}</Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                {item.outputBytes != null && `${formatBytes(item.outputBytes)} checked. `}{item.createdAt && formatDate(item.createdAt)}
              </p>
              {item.failureCodes.length > 0 && <p className="flex flex-wrap gap-1">{item.failureCodes.map((code) => <code key={code} className="rounded bg-muted px-1.5 py-0.5 text-xs break-all">{code}</code>)}</p>}
              {item.checks && item !== f.validation.at(-1) && <CheckList checks={item.checks} />}
            </div>
          ))}
        </Tabs.Content>

        <Tabs.Content value="scan" className="space-y-4 px-5 py-4 outline-none sm:px-6">
          {!f?.analysis ? <Muted>The first scan has no result yet.</Muted> : (
            <>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge tone={f.analysis.detection === "CLEAN" ? "good" : f.analysis.detection === "MALICIOUS" ? "bad" : "warn"}>{f.analysis.detection}</Badge>
                <Badge>{f.analysis.policyDecision}</Badge>
              </div>
              {f.analysis.findings.length === 0 ? <Muted>No check reported anything on the original.</Muted> : (
                <ul className="space-y-2" data-testid="findings">
                  {f.analysis.findings.map((item, index) => (
                    <li key={index} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge tone={item.severity === "CRITICAL" ? "bad" : item.severity === "ERROR" || item.severity === "HIGH" ? "warn" : "muted"}>{item.severity}</Badge>
                      <code className="text-xs break-all">{item.code}</code>
                      <span className="text-muted-foreground">{item.message}</span>
                    </li>
                  ))}
                </ul>
              )}
              <CheckList checks={f.analysis.engines} />
              <dl className="divide-y divide-border">
                <Fact label="Tool versions">
                  <span className="text-xs text-muted-foreground">{f.analysis.engines.map((engine) => `${engine.engine ?? "?"} ${engine.version ?? ""}${engine.databaseVersion ? ` (db ${engine.databaseVersion})` : ""}`.trim()).join(" · ")}</span>
                </Fact>
              </dl>
            </>
          )}
          {f?.signature && f.signature.signatureCount > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-medium">Digital signatures: {f.signature.state}</h3>
              <ul className="space-y-1 text-sm">
                {f.signature.signatures.map((signature, index) => (
                  <li key={index}>{signature.integrity} · {signature.cryptographicValidity} · {signature.trust} · {signature.revocation} · {signature.modificationLevel}
                    {signature.signerSubject && <span className="block text-xs text-muted-foreground">Signer: {signature.signerSubject}</span>}</li>
                ))}
              </ul>
            </div>
          )}
        </Tabs.Content>

        <Tabs.Content value="log" className="space-y-3 px-5 py-4 outline-none sm:px-6" data-testid="log">
          <div className="flex items-center justify-between gap-2">
            <Muted className="text-xs">Every step and retry, oldest first. Copy it to report a problem; it holds no document content.</Muted>
            <Button type="button" size="sm" variant="secondary" onClick={() => void copyLog()}><ClipboardCopy /> Copy</Button>
          </div>
          <ol className="divide-y divide-border rounded-lg border border-border bg-background/40">
            {f?.steps.map((step, index) => {
              const ms = stepMs(step);
              return (
                <li key={index} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2">
                  <span className="min-w-0 text-sm">{STAGE_NAME[step.type] ?? step.type}{step.attempt > 1 ? `, try ${step.attempt}` : ""}
                    <span className="block text-xs text-muted-foreground">{formatDate(step.startedAt)}</span></span>
                  <span className="flex flex-wrap items-center gap-2">
                    {ms != null && <span className="text-xs text-muted-foreground tabular-nums">{formatDuration(ms)}</span>}
                    {step.errorCode && <code className="text-xs break-all text-warning">{step.errorCode}</code>}
                    <Badge tone={step.status === "SUCCEEDED" ? "good" : step.status === "FAILED" ? "warn" : "info"}>{step.status}</Badge>
                  </span>
                </li>
              );
            })}
          </ol>
        </Tabs.Content>

        <Tabs.Content value="ids" className="px-5 py-2 outline-none sm:px-6">
          <dl className="divide-y divide-border">
            <Fact label="Result">{scan.detection} · {scan.policy}</Fact>
            <Fact label="Uploaded">{formatDate(scan.createdAt)}</Fact>
            {doc && <Fact label="Archived">{formatDate(doc.publishedAt)}</Fact>}
            {sidecar?.organization.reason && <Fact label="Filing">{sidecar.organization.reason}</Fact>}
            {sidecar && <Fact label="Metadata revision">{sidecar.metadataRevision}</Fact>}
            <Fact label="Scan ID" copy={scan.scanId}><code className="text-xs break-all">{scan.scanId}</code></Fact>
            {documentId && <Fact label="Document ID" copy={documentId}><code className="text-xs break-all">{documentId}</code></Fact>}
            <Fact label="SHA-256 of the original" copy={scan.sourceSha256}><code className="text-xs break-all">{scan.sourceSha256}</code></Fact>
            {doc && <Fact label="SHA-256 of the clean copy" copy={doc.sha256}><code className="text-xs break-all">{doc.sha256}</code></Fact>}
          </dl>
          {f && <ul className="my-3 list-disc space-y-1 pl-5 text-xs text-muted-foreground">{f.limitations.map((text) => <li key={text}>{text}</li>)}</ul>}
          {documentId && <a className="mb-3 inline-block text-xs text-primary underline-offset-4 hover:underline" href={`/api/v1/archive/${encodeURIComponent(documentId)}/sidecar`} download>Download the metadata file</a>}
        </Tabs.Content>
      </Tabs.Root>
    </section>
  );
}

