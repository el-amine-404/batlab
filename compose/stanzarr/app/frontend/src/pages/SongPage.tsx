import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, AudioLines, Cable, Disc3, Download, Info, Loader2, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { StatusBadge } from "@/components/library/StatusBadge";
import { Failed, Loading } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorText, type LyricsFile } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { clock, FORMAT, length, LEVELS, SOURCE, timings } from "@/lib/format";
import { libraryLink } from "@/lib/librarySearch";
import { useEditor, useLyricsChanged, useSong } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

function Section({ title, children, actions, className }: { title: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <Card className={cn("p-4 sm:p-5", className)}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {actions}
      </div>
      {children}
    </Card>
  );
}

type Line = { t: number; s: string; u?: boolean; fixed?: boolean };

/** Lines as they scroll: the time and the words on one baseline, the unsure ones marked. */
function Lines({ lines, synced, className }: { lines: Line[]; synced: boolean; className?: string }) {
  if (!lines.length) return <p className="text-sm text-muted-foreground">This version has no line.</p>;
  return (
    <ol className={cn("max-h-[60vh] overflow-y-auto pr-1 text-sm leading-6", className)}>
      {lines.map((line, n) => (
        <li key={n} className={cn("grid items-baseline gap-3 rounded px-1.5", synced ? "grid-cols-[5.5rem_1fr]" : "grid-cols-1",
                                  line.u && !line.fixed && "bg-warning/10")}>
          {synced && <span className="font-mono text-xs text-muted-foreground tabular-nums">{clock(line.t)}</span>}
          <span className={cn(!line.s && "text-muted-foreground/50")}>
            {line.s || "♪"}
            {line.u && !line.fixed && <span className="ml-2 text-[11px] text-warning">unsure</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

const versionName = (file: LyricsFile) => `${FORMAT[file.format] ?? file.format} · ${SOURCE[file.source] ?? file.source}`;

function Version({ songId, file }: { songId: number; file: LyricsFile }) {
  const { data, error, isPending } = useQuery({ queryKey: ["song", songId, "file", file.id], queryFn: () => api.fileLines(songId, file.id) });
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} />;
  return <Lines lines={data.lines} synced={data.synced} />;
}

/** Two versions side by side, line by line. */
function Compare({ songId, files }: { songId: number; files: LyricsFile[] }) {
  const [left, setLeft] = useState(files[0].id);
  const [right, setRight] = useState(files[1].id);
  const pick = (value: number, set: (n: number) => void, label: string) => (
    <select value={value} onChange={(e) => set(Number(e.target.value))} aria-label={label}
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm">
      {files.map((f) => <option key={f.id} value={f.id}>{f.name} ({versionName(f)})</option>)}
    </select>
  );
  const one = files.find((f) => f.id === left)!;
  const two = files.find((f) => f.id === right)!;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-2">{pick(left, setLeft, "First version")}<Version songId={songId} file={one} /></div>
      <div className="flex min-w-0 flex-col gap-2">{pick(right, setRight, "Second version")}<Version songId={songId} file={two} /></div>
    </div>
  );
}

/** An action, and when it can't be done now, why, said under it (not only in a tooltip). */
function Action({ children, reason }: { children: ReactNode; reason?: string | null }) {
  return (
    <div className="flex max-w-56 flex-col gap-1">
      {children}
      {reason && <p className="flex items-start gap-1 text-[11px] leading-snug text-muted-foreground"><Info className="mt-px size-3 shrink-0" />{reason}</p>}
    </div>
  );
}

/** One song: what it has and how good, each version to read or download, what was tried, what to do next. */
export function SongPage() {
  const id = Number(useParams().id);
  const can = useCan();
  const changed = useLyricsChanged();
  const { data, error, isPending, refetch } = useSong(id);
  const { data: best } = useEditor(id);
  const [busy, setBusy] = useState<"search" | "ai" | null>(null);
  const [cover, setCover] = useState(true);

  const back = (
    <Link to={libraryLink()} className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> Library
    </Link>
  );
  if (isPending) return <>{back}<Loading /></>;
  if (error) return <>{back}<Failed error={error} retry={() => void refetch()} /></>;
  const { song, files, attempt, jobs, events, quality } = data;

  const act = async (kind: "search" | "ai") => {
    setBusy(kind);
    try {
      if (kind === "search") toast.success((await api.searchSong(id)).result);
      else {
        await api.aiSync(id);
        toast.success("Queued: Activity shows it.");
      }
      changed(id);
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(null);
    }
  };

  const reached = quality.rank >= quality.cutoff && quality.rank !== 9;
  return (
    <>
      {back}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-4">
          {cover ? (
            <img src={api.coverUrl(id)} alt="" onError={() => setCover(false)} className="size-20 shrink-0 rounded-lg object-cover sm:size-24" />
          ) : (
            <span className="flex size-20 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground sm:size-24"><Disc3 className="size-8" /></span>
          )}
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{song.title}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              <Link to={`/library?artist=${encodeURIComponent(song.artist)}`} className="hover:text-foreground hover:underline">{song.artist}</Link>
              {song.album && <> · <Link to={`/library?album=${encodeURIComponent(song.album)}`} className="hover:text-foreground hover:underline">{song.album}</Link></>}
              {" "}· {length(song.duration)}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <StatusBadge status={song.status} linked />
              {quality.rank !== 9 && (
                <span className="text-xs text-muted-foreground" title="The quality ladder, and the cutoff it is searched until (Settings, General)">
                  {LEVELS[quality.rank]} · {reached ? "good enough: no more searching" : `searched until ${LEVELS[quality.cutoff].toLowerCase()}`}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-start gap-3">
          {can("edit") && (
            <Action>
              <Button asChild><Link to={`/songs/${id}/edit`}><Pencil /> {data.has_text ? "Edit timings" : "Write lyrics"}</Link></Button>
            </Action>
          )}
          {can("edit") && (
            <Action reason={data.search_blocked}>
              <Button variant="secondary" onClick={() => void act("search")} disabled={!!busy || !!data.search_blocked}>
                {busy === "search" ? <Loader2 className="animate-spin" /> : <Cable />} Ask the sources
              </Button>
            </Action>
          )}
          {can("ai") && (
            <Action reason={data.ai_blocked}>
              <Button variant="secondary" onClick={() => void act("ai")} disabled={!!busy || !!data.ai_blocked}>
                {busy === "ai" ? <Loader2 className="animate-spin" /> : <AudioLines />} Sync with AI
              </Button>
            </Action>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Section title="Lyrics">
          {!files.length ? (
            <p className="text-sm text-muted-foreground">No lyrics yet. Ask the sources, or write them in the editor.</p>
          ) : (
            <Tabs defaultValue="best">
              <TabsList className="mb-3 flex h-auto flex-wrap justify-start">
                <TabsTrigger value="best" title="The version players get: the best one">As players show it</TabsTrigger>
                {files.map((file) => <TabsTrigger key={file.id} value={String(file.id)} title={file.name}>{versionName(file)}</TabsTrigger>)}
                {files.length > 1 && <TabsTrigger value="compare">Compare</TabsTrigger>}
              </TabsList>
              <TabsContent value="best">
                {!best ? <Loading /> : <Lines lines={best.lines} synced={best.origin !== "plain"} />}
              </TabsContent>
              {files.map((file) => (
                <TabsContent key={file.id} value={String(file.id)}><Version songId={id} file={file} /></TabsContent>
              ))}
              {files.length > 1 && <TabsContent value="compare"><Compare songId={id} files={files} /></TabsContent>}
            </Tabs>
          )}
        </Section>

        <div className="flex min-w-0 flex-col gap-4">
          <Section title="Versions">
            {!files.length ? <p className="text-sm text-muted-foreground">None.</p> : (
              <ul className="flex flex-col divide-y divide-border">
                {files.map((file) => {
                  const timing = timings(file.format, file.synced);
                  const toCheck = file.unsure - file.checked;
                  return (
                    <li key={file.id} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium" title={file.path}>{file.name}</p>
                        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
                          <Link to={`/library?format=${file.format}`} className="hover:text-foreground hover:underline">{FORMAT[file.format] ?? file.format}</Link>
                          <span aria-hidden>·</span>
                          <span title={timing.hint}>Timings: {timing.label.toLowerCase()}</span>
                          <span aria-hidden>·</span>
                          <Link to={`/library?by=${file.source}`} className="hover:text-foreground hover:underline" title="Every song with a file made so">
                            By {SOURCE[file.source] ?? file.source}
                          </Link>
                          <span aria-hidden>·</span>
                          {file.language ? <Link to={`/library?lang=${file.language}`} className="hover:text-foreground hover:underline">{file.language_name}</Link>
                            : <span>language unknown</span>}
                          <span aria-hidden>·</span>
                          <span>{file.lines} {file.lines === 1 ? "line" : "lines"}</span>
                        </p>
                        {file.source === "ai" && (
                          <p className="mt-0.5 text-xs">
                            {file.ai_state !== "unchecked" ? <span className="text-success">Checked by a person</span>
                              : toCheck > 0 ? <span className="text-warning">{toCheck} unsure {toCheck === 1 ? "line" : "lines"} to check</span>
                              : <span className="text-muted-foreground">Not checked by a person yet</span>}
                          </p>
                        )}
                      </div>
                      <Button variant="ghost" size="icon-sm" asChild>
                        <a href={api.fileUrl(id, file.id)} download aria-label={`Download ${file.name}`} title="Download"><Download /></a>
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>

          <Section title="Last search">
            <p className="text-sm text-muted-foreground">
              {attempt ? `${timeAgo(attempt.at)}: ${attempt.result}${attempt.source ? ` (${SOURCE[attempt.source] ?? attempt.source})` : ""}` : "Not asked yet."}
            </p>
          </Section>

          {(jobs.length > 0 || events.length > 0) && (
            <Section title="Activity" actions={<Link to={`/activity?song=${id}`} className="text-xs text-primary hover:underline">All of it</Link>}>
              <ul className="flex flex-col gap-2 text-sm">
                {jobs.map((job) => (
                  <li key={`job-${job.id}`} className="flex justify-between gap-3">
                    <span>AI sync: {job.status}{job.stage ? `, ${job.stage}` : ""}{job.error ? `: ${job.error}` : ""}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(job.finished_at ?? job.created_at)}</span>
                  </li>
                ))}
                {events.map((event) => (
                  <li key={`event-${event.id}`} className="flex justify-between gap-3">
                    <span className={cn("min-w-0", event.ok === false && "text-destructive")}>
                      {event.message.replace(`${song.artist} – ${song.title}: `, "")}
                      {event.actor && <span className="text-muted-foreground"> · {event.actor}</span>}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(event.at)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <div className="flex flex-wrap gap-1.5 px-1">
            {song.isrc && <Badge variant="muted" title="The recording's code, from its tags">ISRC {song.isrc}</Badge>}
            {song.mbid && <Badge variant="muted" title="MusicBrainz's recording, from its tags">MusicBrainz</Badge>}
          </div>
          <p className="truncate px-1 text-xs text-muted-foreground" title={song.path}>{song.rel}</p>
        </div>
      </div>
    </>
  );
}
