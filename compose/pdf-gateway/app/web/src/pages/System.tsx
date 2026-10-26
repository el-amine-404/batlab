import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, Cpu, Database, FileCheck2, ShieldCheck, Sparkles, type LucideIcon } from "lucide-react";
import { api } from "../api";
import { AiSetup, aiUp } from "../components/ai";
import { Badge, Card, ErrorNote, Muted, PageHeader, formatDate, formatWhen, type Tone } from "../components/ui";
import { AI_STATE } from "../words";

/** Is everything working, in words; numbers for the curious. */
export function SystemPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 30_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, refetchInterval: 20_000 });
  const s = summary.data;
  const h = health.data;
  const a = ai.data;
  const coreUp = h && h.database === "UP" && h.broker === "UP" && h.archiveReadable;
  const aiTone: Tone = !a ? "muted" : aiUp(a.state) ? "good" : a.state === "DISABLED" ? "muted" : "warn";
  const languages = a?.ocrLanguages?.filter((code) => code !== "osd").join(", ");

  return (
    <div className="space-y-6">
      <PageHeader title="System" description="Whether every part of the gateway is working, and what it has done." />
      {s && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="stats">
          <Stat icon={FileCheck2} label="Added today" value={s.last24h} />
          <Stat icon={Database} label="In the archive" value={s.archived} />
          <Stat icon={ShieldCheck} label="Held" value={s.quarantined} tone={s.quarantined ? "warn" : undefined} />
          <Stat icon={ShieldCheck} label="Dangerous" value={s.malicious} tone={s.malicious ? "bad" : undefined} />
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Is it working?" icon={<Cpu className="size-5" />}>
          <ErrorNote error={health.error} />
          <ul className="space-y-4 text-sm" data-testid="health">
            <Line tone={!h ? "muted" : coreUp ? "good" : "bad"} label="Checking and archiving"
              text={!h ? "…" : coreUp ? "Working." : "Not working: new files wait until it's back."}
              detail={h && `Database ${h.database} · message broker ${h.broker} · archive ${h.archiveReadable ? "readable" : "unavailable"}`} />
            <Line tone={!h?.cdr?.lastStatus ? "muted" : h.cdr.lastStatus === "SUCCEEDED" ? "good" : "warn"} label="Clean-copy sandbox"
              text={!h?.cdr?.lastStatus ? "No file rebuilt yet." : h.cdr.lastStatus === "SUCCEEDED" ? `Last rebuild worked (${formatWhen(h.cdr.lastAt)}).` : `Last rebuild failed${h.cdr.lastFailureCode ? ` (${h.cdr.lastFailureCode})` : ""}.`}
              detail="Known from the latest file it rebuilt; it isn't probed." />
            <Line tone={h?.clamav?.databaseVersion ? "good" : "warn"} label="Virus signatures"
              text={h?.clamav?.databaseVersion ? `Version ${h.clamav.databaseVersion}` : "Unknown"} detail={h?.clamav?.databaseDate ?? undefined} />
          </ul>
          {s && (
            <Muted className="mt-5 border-t border-border pt-4 text-xs">
              {s.readyLatencySeconds.p50 !== null && <>Files are usually ready in {seconds(s.readyLatencySeconds.p50)}. </>}
              {s.scans} {s.scans === 1 ? "file" : "files"} in total{s.cdrFailures > 0 && `, ${s.cdrFailures} failed ${s.cdrFailures === 1 ? "rebuild" : "rebuilds"}`}.
              {h && h.outbox.pending > 0 && ` ${h.outbox.pending} internal messages waiting (oldest ${Math.round(h.outbox.oldestSeconds)} s).`}
              {h && ` Updated ${formatDate(new Date().toISOString())}.`}
            </Muted>
          )}
        </Card>

        <Card title="AI filing (your laptop)" icon={<Sparkles className="size-5" />}
          description="Reads each new document's text and files it: title, correspondent, type, date, folder."
          actions={a && <Badge tone={aiTone}>{AI_STATE[a.state] ?? a.state}</Badge>}>
          <ErrorNote error={ai.error} />
          {a && (
            <div className="space-y-4" data-testid="ai">
              <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
                <dt className="text-muted-foreground">Model</dt>
                <dd className="flex min-w-0 items-center gap-1.5 break-all"><Bot className="size-4 shrink-0 text-muted-foreground" />{a.model ?? "—"}
                  {a.llmService && <Badge tone={a.llmService === "UP" ? "good" : "warn"} className="ml-1">LM Studio {a.llmService === "UP" ? "up" : "down"}</Badge>}</dd>
                <dt className="text-muted-foreground">Text model</dt>
                <dd>Reads the text only; no vision model looks at the pages.</dd>
                <dt className="text-muted-foreground">OCR</dt>
                <dd>{a.ocrEngine ? <>{a.ocrEngine}{languages && <span className="text-muted-foreground"> · {languages}</span>}</> : "—"}
                  <span className="block text-xs text-muted-foreground">For scans without a text layer, before the model reads them.</span></dd>
                <dt className="text-muted-foreground">Last seen</dt>
                <dd>{formatWhen(a.lastSeenAt)}{a.lastSuccessAt && <span className="text-muted-foreground"> · last filed {formatWhen(a.lastSuccessAt)}</span>}</dd>
                <dt className="text-muted-foreground">Queue</dt>
                <dd>{a.queue.waiting} waiting · {a.queue.running} working{a.queue.failed > 0 && <> · <span className="text-warning">{a.queue.failed} failed</span></>}</dd>
              </dl>
              {!aiUp(a.state) && a.state !== "DISABLED" && <Muted>Files are still checked and archived; their filing waits for the AI (up to 10 minutes each).</Muted>}
              <AiSetup ai={a} />
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function Line({ tone, label, text, detail }: { tone: Tone; label: string; text: ReactNode; detail?: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span className={`mt-1.5 size-2.5 shrink-0 rounded-full ${tone === "good" ? "bg-success shadow-[0_0_10px] shadow-success/60" : tone === "bad" ? "bg-destructive" : tone === "warn" ? "bg-warning" : "bg-muted-foreground/40"}`} aria-hidden />
      <div className="min-w-0">
        <p><span className="font-medium">{label}:</span> {text}</p>
        {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
      </div>
    </li>
  );
}

function Stat({ icon: Icon, label, value, tone }: { icon: LucideIcon; label: string; value: number; tone?: Tone }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 backdrop-blur-xl">
      <div className="flex items-center justify-between text-muted-foreground"><span className="text-xs">{label}</span><Icon className="size-4" /></div>
      <div className={`mt-2 text-3xl font-semibold tabular-nums ${tone === "bad" ? "text-destructive" : tone === "warn" ? "text-warning" : ""}`}>{value}</div>
    </div>
  );
}

function seconds(value: number | null) {
  if (value === null) return "—";
  return value < 120 ? `${Math.round(value)} s` : `${Math.round(value / 60)} min`;
}
