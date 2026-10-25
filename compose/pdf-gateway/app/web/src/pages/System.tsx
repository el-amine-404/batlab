import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Badge, Card, ErrorNote, Muted, formatDate, formatWhen, type Tone } from "../components/ui";
import { AI_STATE } from "../words";

/** Is everything working, in words; numbers for the curious. */
export function SystemPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, refetchInterval: 60_000 });
  const s = summary.data;
  const h = health.data;
  const a = ai.data;
  const coreUp = h && h.database === "UP" && h.broker === "UP" && h.archiveReadable;
  const aiTone: Tone = !a ? "muted" : ["READY", "AVAILABLE", "BUSY"].includes(a.state) ? "good" : a.state === "DISABLED" ? "muted" : "warn";

  return (
    <>
      <h1 className="text-xl font-semibold">System</h1>
      <Card title="Is it working?">
        <ErrorNote error={health.error ?? ai.error} />
        <ul className="space-y-3 text-sm" data-testid="health">
          <Line tone={!h ? "muted" : coreUp ? "good" : "bad"} label="Checking and archiving"
            text={!h ? "…" : coreUp ? "Working." : "Not working: new files wait until it's back."}
            detail={h && `Database ${h.database} · message broker ${h.broker} · archive ${h.archiveReadable ? "readable" : "unavailable"}`} />
          <Line tone={!h?.cdr?.lastStatus ? "muted" : h.cdr.lastStatus === "SUCCEEDED" ? "good" : "warn"} label="Clean-copy sandbox"
            text={!h?.cdr?.lastStatus ? "No file rebuilt yet." : h.cdr.lastStatus === "SUCCEEDED" ? `Last rebuild worked (${formatWhen(h.cdr.lastAt)}).` : `Last rebuild failed${h.cdr.lastFailureCode ? ` (${h.cdr.lastFailureCode})` : ""}.`}
            detail="Known from the latest file it rebuilt; it isn't probed." />
          <Line tone={h?.clamav?.databaseVersion ? "good" : "warn"} label="Virus signatures"
            text={h?.clamav?.databaseVersion ? `Version ${h.clamav.databaseVersion}` : "Unknown"} detail={h?.clamav?.databaseDate ?? undefined} />
          <li data-testid="ai">
            <Line tone={aiTone} label="AI suggestions (laptop)" text={a ? AI_STATE[a.state] ?? a.state : "…"}
              detail={a && a.state !== "DISABLED" ? <>
                {!["READY", "AVAILABLE", "BUSY"].includes(a.state) && "Files are still checked and archived; suggestions wait. "}
                Waiting {a.queue.waiting} · working {a.queue.running} · failed {a.queue.failed}
                {a.model && <> · {a.model}, last seen {formatWhen(a.lastSeenAt)}</>}
              </> : undefined} bare />
          </li>
        </ul>
      </Card>
      <Card title="Activity">
        <ErrorNote error={summary.error} />
        {s && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="stats">
              <Stat label="Added today" value={s.last24h} />
              <Stat label="In the archive" value={s.archived} />
              <Stat label="Held" value={s.quarantined} />
              <Stat label="Dangerous" value={s.malicious} />
            </div>
            <Muted className="mt-3">
              {s.readyLatencySeconds.p50 !== null && <>Files are usually ready in {seconds(s.readyLatencySeconds.p50)}. </>}
              {s.scans} {s.scans === 1 ? "file" : "files"} in total{s.cdrFailures > 0 && `, ${s.cdrFailures} failed ${s.cdrFailures === 1 ? "rebuild" : "rebuilds"}`}.
            </Muted>
          </>
        )}
        {h && h.outbox.pending > 0 && <Muted className="mt-1 text-xs">{h.outbox.pending} internal messages waiting (oldest {Math.round(h.outbox.oldestSeconds)} s).</Muted>}
        {h && <Muted className="mt-1 text-xs">Updated {formatDate(new Date().toISOString())}</Muted>}
      </Card>
    </>
  );
}

function Line({ tone, label, text, detail, bare }: { tone: Tone; label: string; text: ReactNode; detail?: ReactNode; bare?: boolean }) {
  const body = (
    <div className="flex items-start gap-3">
      <Badge tone={tone}>{tone === "good" ? "OK" : tone === "bad" ? "Down" : tone === "warn" ? "Check" : "—"}</Badge>
      <div className="min-w-0">
        <p><span className="font-medium">{label}:</span> {text}</p>
        {detail && <p className="text-xs text-slate-500 dark:text-slate-400">{detail}</p>}
      </div>
    </div>
  );
  return bare ? body : <li>{body}</li>;
}

function Stat({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800"><div className="text-2xl font-semibold">{value}</div><div className="text-xs text-slate-500 dark:text-slate-400">{label}</div></div>;
}

function seconds(value: number | null) {
  if (value === null) return "—";
  return value < 120 ? `${Math.round(value)} s` : `${Math.round(value / 60)} min`;
}
