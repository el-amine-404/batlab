import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, FileSearch, Inbox, ScanText, Send, Sparkles } from "lucide-react";
import { Link } from "react-router";
import { api, type ActivityJob } from "../api";
import { aiUp } from "../components/ai";
import { Badge, Card, ErrorNote, Muted, PageHeader, Spinner, formatWhen, type Tone } from "../components/ui";
import { AI_STATE, statusWord } from "../words";

const JOB: Record<string, { text: string; tone: Tone }> = {
  WAITING_FOR_AI: { text: "Waiting", tone: "muted" }, LEASED: { text: "Running", tone: "info" }, SUBMITTED: { text: "Checking the answer", tone: "info" },
  COMPLETED: { text: "Done", tone: "good" }, FAILED: { text: "Failed", tone: "bad" }, SKIPPED: { text: "Skipped", tone: "muted" }, EXPIRED: { text: "Gave up", tone: "muted" },
};
const PAPERLESS: Record<string, { text: string; tone: Tone }> = {
  PENDING: { text: "Waiting", tone: "muted" }, IMPORTING: { text: "Sending", tone: "info" }, IMPORTED: { text: "Added", tone: "good" },
  FAILED_RETRYABLE: { text: "Trying again", tone: "warn" }, FAILED_FINAL: { text: "Refused", tone: "bad" },
};

/** How long, in words: "40 s", "3 min", "2 h". */
function span(from: string, to: string) {
  const seconds = Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000);
  return seconds < 90 ? `${Math.round(seconds)} s` : seconds < 5400 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds / 3600)} h`;
}

/** What the gateway is doing now (files being checked, the AI laptop's queue, Paperless), and lately. */
export function ActivityPage() {
  const activity = useQuery({
    queryKey: ["activity"], queryFn: api.activity,
    refetchInterval: (query) => query.state.data && (query.state.data.files.length || query.state.data.ai.some((job) => job.active)) ? 4_000 : 20_000,
  });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, refetchInterval: 20_000 });
  const data = activity.data;
  const waiting = data?.ai.filter((job) => job.status === "WAITING_FOR_AI").sort((a, b) => a.createdAt.localeCompare(b.createdAt)) ?? [];
  const running = data?.ai.filter((job) => job.active && job.status !== "WAITING_FOR_AI") ?? [];
  const history = data?.ai.filter((job) => !job.active) ?? [];
  const sending = data?.paperless.filter((item) => item.status !== "IMPORTED") ?? [];
  const idle = data && !data.files.length && !running.length && !waiting.length && !sending.length;

  return (
    <div className="space-y-6">
      <PageHeader title="Activity" description="What the gateway is doing now, and what it did lately: the checks, the AI laptop's queue, and Paperless." />
      <ErrorNote error={activity.error} />
      {idle && (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-5 backdrop-blur-xl" data-testid="activity-idle">
          <CheckCircle2 className="size-6 text-success" /><span>Nothing running. New files start here as soon as they're added.</span>
        </div>
      )}

      {data && data.files.length > 0 && (
        <Card title={`Being checked (${data.files.length})`} icon={<FileSearch className="size-5" />}>
          <ul className="divide-y divide-border">
            {data.files.map((file) => {
              const word = statusWord(file.status as Parameters<typeof statusWord>[0]);
              return (
                <Line key={file.scanId} to={`/documents/${file.scanId}`} title={file.title}
                  meta={`added ${formatWhen(file.createdAt)} · in this step for ${span(file.updatedAt, data.now)}`}
                  badge={<Badge tone={word.tone}>{word.tone === "info" && <Spinner />}{word.text}</Badge>} />
              );
            })}
          </ul>
        </Card>
      )}

      {data && (running.length > 0 || waiting.length > 0) && (
        <Card title="The AI laptop's queue" icon={<Sparkles className="size-5" />}
          description={ai.data && !aiUp(ai.data.state) ? `${AI_STATE[ai.data.state] ?? ai.data.state}: these wait for it.` : "One at a time, in order."}>
          <ul className="divide-y divide-border" data-testid="ai-queue">
            {running.map((job) => <JobLine key={`${job.scanId}-${job.kind}`} job={job} now={data.now} />)}
            {waiting.map((job, index) => <JobLine key={`${job.scanId}-${job.kind}`} job={job} now={data.now} position={index + 1} />)}
          </ul>
        </Card>
      )}

      {sending.length > 0 && (
        <Card title="On the way to Paperless" icon={<Send className="size-5" />}>
          <ul className="divide-y divide-border">
            {sending.map((item) => (
              <Line key={item.scanId} to={`/documents/${item.scanId}`} title={item.title ?? item.scanId}
                meta={`${item.attempts} ${item.attempts === 1 ? "try" : "tries"}${item.lastError ? ` · ${item.lastError}` : ""} · ${formatWhen(item.updatedAt)}`}
                badge={<Badge tone={PAPERLESS[item.status]?.tone ?? "muted"}>{PAPERLESS[item.status]?.text ?? item.status}</Badge>} />
            ))}
          </ul>
        </Card>
      )}

      <Card title="Lately" icon={<Inbox className="size-5" />} description="The AI laptop's jobs of the last two weeks.">
        {activity.isPending && <Muted className="flex items-center gap-2"><Spinner /> Loading…</Muted>}
        {data && history.length === 0 && <Muted>None yet.</Muted>}
        <ul className="divide-y divide-border" data-testid="ai-history">
          {data && history.map((job) => <JobLine key={`${job.scanId}-${job.kind}-${job.createdAt}`} job={job} now={data.now} />)}
        </ul>
      </Card>
    </div>
  );
}

function JobLine({ job, now, position }: { job: ActivityJob; now: string; position?: number }) {
  const kind = job.kind === "OCR" ? "Read the pages (OCR)" : "Filing";
  const who = [job.model ?? (job.kind === "OCR" ? job.engine : null), job.agent && `on ${job.agent}`].filter(Boolean).join(" ");
  const when = position ? `#${position} in line · asked ${formatWhen(job.createdAt)}`
    : job.status === "LEASED" ? `running for ${job.leaseUntil ? span(new Date(Date.parse(job.leaseUntil) - 600_000).toISOString(), now) : "a moment"}`
    : job.completedAt ? `${formatWhen(job.completedAt)}` : formatWhen(job.createdAt);
  const state = JOB[job.status] ?? { text: job.status, tone: "muted" as Tone };
  return (
    <Line to={`/documents/${job.scanId}`} title={job.title ?? job.scanId}
      icon={job.kind === "OCR" ? <ScanText className="size-4" /> : <Bot className="size-4" />}
      meta={<>{kind}{who && <> · {who}</>} · {when}{job.errorCode && <span className="text-destructive"> · {job.errorCode}</span>}</>}
      badge={<Badge tone={state.tone}>{state.tone === "info" && <Spinner />}{state.text}</Badge>} />
  );
}

function Line({ to, title, meta, badge, icon }: { to: string; title: string; meta: ReactNode; badge: ReactNode; icon?: ReactNode }) {
  return (
    <li>
      <Link to={to} className="-mx-3 flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-accent/60">
        {icon && <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">{icon}</span>}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{title}</span>
          <span className="block truncate text-xs text-muted-foreground">{meta}</span>
        </span>
        <span className="shrink-0">{badge}</span>
      </Link>
    </li>
  );
}
