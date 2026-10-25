import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Badge, Card, ErrorNote, STATUS_LABEL, formatDate } from "../components/ui";

export function DashboardPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const s = summary.data;
  const h = health.data;
  return (
    <>
      <Card title="Activity">
        <ErrorNote error={summary.error} />
        {s && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="stats">
              <Stat label="Uploads (24 h)" value={s.last24h} />
              <Stat label="Archived" value={s.archived} />
              <Stat label="Held" value={s.quarantined} />
              <Stat label="Malicious" value={s.malicious} />
            </div>
            <p className="mt-3 text-sm text-slate-600">
              Time to ready: median {seconds(s.readyLatencySeconds.p50)}, 95th percentile {seconds(s.readyLatencySeconds.p95)} · CDR failures {s.cdrFailures} · {s.scans} uploads total
            </p>
            <ul className="mt-2 flex flex-wrap gap-2">{Object.entries(s.byStatus).map(([status, n]) => <li key={status}><Badge>{STATUS_LABEL[status] ?? status}: {n}</Badge></li>)}</ul>
          </>
        )}
      </Card>
      <Card title="System health">
        <ErrorNote error={health.error} />
        {h && (
          <ul className="space-y-1 text-sm" data-testid="health">
            <li>Database <Badge tone={h.database === "UP" ? "good" : "bad"}>{h.database}</Badge></li>
            <li>Message broker <Badge tone={h.broker === "UP" ? "good" : "bad"}>{h.broker}</Badge></li>
            <li>Archive <Badge tone={h.archiveReadable ? "good" : "bad"}>{h.archiveReadable ? "readable" : "unavailable"}</Badge></li>
            <li>Outbox backlog: {h.outbox.pending}{h.outbox.pending > 0 ? ` (oldest ${Math.round(h.outbox.oldestSeconds)} s)` : ""}</li>
            <li>In progress: {Object.entries(h.pipeline).map(([status, n]) => `${STATUS_LABEL[status] ?? status} ${n}`).join(", ") || "nothing"}</li>
            <li>ClamAV definitions: {h.clamav?.databaseVersion ?? "unknown"} {h.clamav?.databaseDate ? `(${h.clamav.databaseDate})` : ""}</li>
            <li>Last CDR: {h.cdr?.lastStatus ?? "none yet"}{h.cdr?.lastFailureCode ? ` (${h.cdr.lastFailureCode})` : ""} {h.cdr?.lastAt ? formatDate(h.cdr.lastAt) : ""}</li>
          </ul>
        )}
        {h && <ul className="mt-2 list-disc pl-5 text-xs text-slate-500">{h.limitations.map((text) => <li key={text}>{text}</li>)}</ul>}
      </Card>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200"><div className="text-2xl font-semibold">{value}</div><div className="text-xs text-slate-500">{label}</div></div>;
}

function seconds(value: number | null) {
  if (value === null) return "—";
  return value < 120 ? `${Math.round(value)} s` : `${Math.round(value / 60)} min`;
}
