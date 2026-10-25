import { useEffect, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, Scan } from "../api";
import { Badge, Card, ErrorNote, StatusBadge, TERMINAL, formatBytes, formatDate } from "../components/ui";

const STAGES = ["ANALYSIS", "SIGNATURE", "CDR", "VALIDATION", "ARCHIVE"];

export function ScanDetailPage() {
  const { scanId = "" } = useParams();
  const client = useQueryClient();
  const scan = useQuery({
    queryKey: ["scan", scanId], queryFn: () => api.scan(scanId),
    // Fallback when server-sent events are unavailable (e.g. a proxy buffers them).
    refetchInterval: (query) => query.state.data && TERMINAL.has(query.state.data.status) ? false : 5_000,
  });
  const findings = useQuery({ queryKey: ["findings", scanId, scan.data?.status], queryFn: () => api.findings(scanId), enabled: !!scan.data });

  useEffect(() => {
    if (!scan.data || TERMINAL.has(scan.data.status)) return;
    const source = new EventSource(`/api/v1/scans/${encodeURIComponent(scanId)}/events`);
    source.addEventListener("status", (event) => {
      const parsed = Scan.safeParse(JSON.parse((event as MessageEvent).data));
      if (parsed.success) client.setQueryData(["scan", scanId], parsed.data);
    });
    source.onerror = () => source.close();
    return () => source.close();
  }, [scanId, scan.data?.status, client]);

  if (scan.error) return <ErrorNote error={scan.error} />;
  if (!scan.data) return <p className="text-sm text-slate-500">Loading…</p>;
  const view = scan.data;
  const f = findings.data;
  const doneStages = new Set(f?.steps.filter((step) => step.status === "SUCCEEDED").map((step) => step.type));

  return (
    <>
      <Card title={<span className="break-all" data-testid="scan-name">{view.displayName}</span>} actions={<StatusBadge status={view.status} />}>
        <ol className="mb-4 flex flex-wrap gap-2" aria-label="Pipeline stages">
          {STAGES.map((stage) => <li key={stage}><Badge tone={doneStages.has(stage) ? "good" : "muted"}>{stage.toLowerCase()}</Badge></li>)}
        </ol>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Row label="Detection" value={view.detection} />
          <Row label="Policy" value={view.policy} />
          <Row label="Artifact" value={view.artifact} />
          <Row label="Stage" value={view.stage} />
          <Row label="Size" value={formatBytes(view.sourceBytes)} />
          <Row label="Uploaded" value={formatDate(view.createdAt)} />
          <Row label="SHA-256 (original)" value={<code className="break-all text-xs">{view.sourceSha256}</code>} />
        </dl>
        {view.originalRetention === "SIGNED_RESTRICTED" && (
          <p className="mt-3 rounded-lg bg-sky-50 p-3 text-sm text-sky-900 ring-1 ring-sky-200">
            The original is cryptographically signed. It is kept restricted as evidence; the released copy is a visual reconstruction and is not signed.
          </p>
        )}
        {f?.archive && view.status === "READY" && (
          <p className="mt-3 text-sm">Released to the archive: <Link className="font-medium underline" to={`/archive/${f.archive.documentId}`}>open document</Link> ({f.archive.category}, {f.archive.reviewState.toLowerCase()})</p>
        )}
      </Card>

      <ErrorNote error={findings.error} />
      {f?.analysis && (
        <Card title="Scan findings">
          {f.analysis.findings.length === 0 ? <p className="text-sm text-slate-500">No indicators reported.</p> : (
            <ul className="space-y-1 text-sm" data-testid="findings">
              {f.analysis.findings.map((item, index) => (
                <li key={index} className="flex flex-wrap items-center gap-2">
                  <Badge tone={item.severity === "CRITICAL" ? "bad" : item.severity === "ERROR" || item.severity === "HIGH" ? "warn" : "muted"}>{item.severity}</Badge>
                  <span className="font-mono text-xs">{item.code}</span>
                  <span className="text-slate-600">{item.message}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-slate-500">
            Engines: {f.analysis.engines.map((engine) => `${engine.engine ?? "?"} ${engine.version ?? ""}${engine.databaseVersion ? ` (db ${engine.databaseVersion})` : ""}`.trim()).join(" · ") || "—"}
          </p>
        </Card>
      )}
      {f?.signature && (
        <Card title="Digital signatures">
          <p className="text-sm"><Badge tone={f.signature.state.startsWith("SIGNED_VALID") ? "good" : f.signature.state === "UNSIGNED" ? "muted" : "warn"}>{f.signature.state}</Badge> {f.signature.signatureCount} signature(s)</p>
          <ul className="mt-2 space-y-1 text-sm">
            {f.signature.signatures.map((signature, index) => (
              <li key={index}>{signature.integrity} · {signature.cryptographicValidity} · {signature.trust} · {signature.revocation} · {signature.modificationLevel}
                {signature.signerSubject && <span className="block text-xs text-slate-500">Signer: {signature.signerSubject}</span>}</li>
            ))}
          </ul>
          <ul className="mt-2 list-disc pl-5 text-xs text-slate-500">{f.signature.limitations.map((text) => <li key={text}>{text}</li>)}</ul>
        </Card>
      )}
      {f && (f.cdr.length > 0 || f.validation.length > 0) && (
        <Card title="Rebuild and final checks">
          <ul className="space-y-1 text-sm">
            {f.cdr.map((item) => <li key={`c${item.attempt}`}>CDR attempt {item.attempt}: {item.status}{item.failureCode ? ` (${item.failureCode})` : ""}{item.durationMs ? ` · ${(item.durationMs / 1000).toFixed(1)} s` : ""}</li>)}
            {f.validation.map((item) => <li key={`v${item.attempt}`}>Final check {item.attempt} ({item.artifact === "EMBEDDED_METADATA" ? "with metadata" : "rebuilt PDF"}): <Badge tone={item.verdict === "PASS" ? "good" : "warn"}>{item.verdict}</Badge>{item.failureCodes.length > 0 && <span className="ml-2 font-mono text-xs">{item.failureCodes.join(", ")}</span>}</li>)}
          </ul>
        </Card>
      )}
      {f && <ul className="list-disc pl-5 text-xs text-slate-500">{f.limitations.map((text) => <li key={text}>{text}</li>)}</ul>}
    </>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return <div className="min-w-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="break-words">{value}</dd></div>;
}
