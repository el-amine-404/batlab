import { ArrowLeft, AudioLines, Cable, FileText, Loader2, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { StatusBadge } from "@/components/library/StatusBadge";
import { Failed, Loading } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorText } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { clock, FORMAT, length, SOURCE } from "@/lib/format";
import { useEditor, useLyricsChanged, useSong, useStatus } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <Card className="p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {actions}
      </div>
      {children}
    </Card>
  );
}

/** One song: its lyrics as they will scroll, what files it has, what was tried, and what to do next. */
export function SongPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const can = useCan();
  const changed = useLyricsChanged();
  const { data, error, isPending, refetch } = useSong(id);
  const { data: lyrics } = useEditor(id);
  const { data: status } = useStatus();
  const [busy, setBusy] = useState<"search" | "ai" | null>(null);

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const { song, files, attempt, jobs, events } = data;
  const queued = jobs.some((job) => job.status === "queued" || job.status === "running");
  const gpu = !!status?.gpu_online;

  const act = async (kind: "search" | "ai") => {
    setBusy(kind);
    try {
      if (kind === "search") toast.success((await api.searchSong(id)).result);
      else {
        await api.aiSync(id);
        toast.success("Queued: the GPU worker takes it next.");
      }
      changed(id);
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <button type="button" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate("/"))}
              className="mb-4 flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Library
      </button>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{song.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {song.artist}{song.album ? ` · ${song.album}` : ""} · {length(song.duration)}
          </p>
          <div className="mt-2"><StatusBadge status={song.status} /></div>
        </div>
        <div className="flex flex-wrap gap-2">
          {can("edit") && (
            <Button asChild><Link to={`/songs/${id}/edit`}><Pencil /> {data.has_text ? "Edit timings" : "Write lyrics"}</Link></Button>
          )}
          {can("edit") && (
            <Button variant="secondary" onClick={() => void act("search")} disabled={!!busy}>
              {busy === "search" ? <Loader2 className="animate-spin" /> : <Cable />} Ask the sources
            </Button>
          )}
          {can("ai") && (
            <Button variant="secondary" onClick={() => void act("ai")} disabled={!!busy || queued || !data.has_text || !gpu}
                    title={!data.has_text ? "AI sync places words already there: this song has none" : !gpu ? "No GPU worker is online"
                      : queued ? "Queued already" : "Place every line with AI, on the GPU worker"}>
              {busy === "ai" ? <Loader2 className="animate-spin" /> : <AudioLines />} {queued ? "AI sync queued" : "Sync with AI"}
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Section title="Lyrics" actions={lyrics && lyrics.lines.length > 0 && (
          <span className="text-xs text-muted-foreground">{lyrics.lines.length} lines · {lyrics.origin === "plain" ? "not synced" : SOURCE[lyrics.origin] ?? lyrics.origin}</span>
        )}>
          {!lyrics ? <Loading /> : !lyrics.lines.length ? (
            <p className="text-sm text-muted-foreground">No lyrics yet. Ask the sources, or write them in the editor.</p>
          ) : (
            <ol className="max-h-[60vh] overflow-y-auto pr-1 text-sm leading-relaxed">
              {lyrics.lines.map((line, n) => (
                <li key={n} className={cn("flex gap-3 rounded px-1.5 py-0.5", line.u && !line.fixed && "bg-warning/10")}>
                  {lyrics.origin !== "plain" && <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{clock(line.t)}</span>}
                  <span className={cn(line.u && !line.fixed && "text-warning")}>{line.s}</span>
                </li>
              ))}
            </ol>
          )}
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="Files beside it">
            {!files.length ? <p className="text-sm text-muted-foreground">None.</p> : (
              <ul className="flex flex-col gap-2.5">
                {files.map((file) => (
                  <li key={file.id} className="flex items-start gap-2.5 text-sm">
                    <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate" title={file.path}>{file.name}</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <Badge variant="muted">{FORMAT[file.format] ?? file.format}</Badge>
                        <Badge variant={file.synced ? "accent" : "muted"}>{file.synced ? "Synced" : "Plain"}</Badge>
                        {file.language && <Badge variant="muted">{file.language_name}</Badge>}
                        <Badge variant="muted">{SOURCE[file.source] ?? file.source}</Badge>
                        {file.source === "ai" && (file.ai_state === "unchecked"
                          ? file.unsure > 0 && <Badge variant="warning">{file.unsure} unsure lines to check</Badge>
                          : <Badge variant="success">Checked by a person</Badge>)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Last search">
            <p className="text-sm text-muted-foreground">
              {attempt ? `${timeAgo(attempt.at)}: ${attempt.result}${attempt.source ? ` (${attempt.source})` : ""}` : "Not asked yet."}
            </p>
          </Section>

          {(jobs.length > 0 || events.length > 0) && (
            <Section title="Activity">
              <ul className="flex flex-col gap-2 text-sm">
                {jobs.map((job) => (
                  <li key={`job-${job.id}`} className="flex justify-between gap-3">
                    <span>AI sync: {job.status}{job.stage ? `, ${job.stage}` : ""}{job.error ? `: ${job.error}` : ""}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(job.finished_at ?? job.created_at)}</span>
                  </li>
                ))}
                {events.map((event) => (
                  <li key={`event-${event.id}`} className="flex justify-between gap-3">
                    <span className="min-w-0">{event.message.replace(`${song.artist} – ${song.title}: `, "")}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(event.at)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <p className="truncate px-1 text-xs text-muted-foreground" title={song.path}>{song.rel}</p>
        </div>
      </div>
    </>
  );
}
