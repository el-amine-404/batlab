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
    <ol className={cn("text-sm leading-6", className)}>
      {lines.map((line, n) => (
        <li key={n} className={cn("grid items-baseline gap-3 rounded px-1.5", synced ? "grid-cols-[3.5rem_1fr]" : "grid-cols-1",
                                  line.u && !line.fixed && "bg-warning/10")}>
          {synced && <span className="font-mono text-[11px] text-muted-foreground tabular-nums">{clock(line.t).replace(/\.\d+$/, "")}</span>}
          <span className={cn(!line.s && "text-muted-foreground/50")}>
            {line.s || "♪"}
            {line.u && !line.fixed && <span className="ml-2 text-[11px] text-warning">unsure</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** What a version is, in words: "LRC file", "Embedded in the audio", with who made it when the file says. */
function versionName(file: LyricsFile): string {
  const what = file.format === "embedded" ? "Embedded" : `${FORMAT[file.format] ?? file.format} file`;
  return file.source === "unknown" ? what : `${what} · ${SOURCE[file.source] ?? file.source}`;
}

function Version({ songId, file }: { songId: number; file: LyricsFile }) {
  const { data, error, isPending } = useVersion(songId, file);
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} />;
  return <Lines lines={data.lines} synced={data.synced} />;
}

type Pair = { a?: Line; b?: Line };

const plainWords = (text: string) => text.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** The two versions' lines matched up (the longest run of the same words), the rest on their own. */
function pairUp(a: Line[], b: Line[]): Pair[] {
  const x = a.map((l) => plainWords(l.s));
  const y = b.map((l) => plainWords(l.s));
  const table = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      table[i][j] = x[i] === y[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const out: Pair[] = [];
  let i = 0, j = 0;
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) out.push({ a: a[i++], b: b[j++] });
    else if (j >= y.length || (i < x.length && table[i + 1][j] >= table[i][j + 1])) out.push({ a: a[i++] });
    else out.push({ b: b[j++] });
  }
  return out;
}

function useVersion(songId: number, file: LyricsFile) {
  return useQuery({ queryKey: ["song", songId, "file", file.id], queryFn: () => api.fileLines(songId, file.id) });
}

/**
 * Two versions against each other, one list: lines both have, with how far
 * apart their times are, and lines only one has, marked with which.
 */
function Compare({ songId, files }: { songId: number; files: LyricsFile[] }) {
  const [left, setLeft] = useState(files[0].id);
  const [right, setRight] = useState(files[1].id);
  const one = useVersion(songId, files.find((f) => f.id === left)!);
  const two = useVersion(songId, files.find((f) => f.id === right)!);
  const pick = (value: number, set: (n: number) => void, label: string, tone: string) => (
    <label className="flex min-w-0 flex-1 items-center gap-2 text-xs text-muted-foreground">
      <span className={cn("size-2.5 shrink-0 rounded-full", tone)} aria-hidden />
      <select value={value} onChange={(e) => set(Number(e.target.value))} aria-label={label}
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground">
        {files.map((f) => <option key={f.id} value={f.id}>{versionName(f)}</option>)}
      </select>
    </label>
  );
  const pairs = one.data && two.data ? pairUp(one.data.lines, two.data.lines) : null;
  const both = pairs?.filter((p) => p.a && p.b) ?? [];
  // Times compared only when both have them.
  const timed = !!(one.data?.synced && two.data?.synced);
  const gaps = timed ? both.map((p) => Math.abs(p.a!.t - p.b!.t)) : [];
  const moved = gaps.filter((g) => g >= 0.3).length;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        {pick(left, setLeft, "First version", "bg-sky-400")}
        {pick(right, setRight, "Second version", "bg-amber-400")}
      </div>
      {!pairs ? <Loading /> : left === right ? <p className="text-sm text-muted-foreground">Pick two different versions.</p> : (
        <>
          <p className="text-xs text-muted-foreground">
            {both.length} lines in both{!timed ? " (one has no timings)" : moved ? `, ${moved} timed differently (0.3 s or more)` : ", timed the same"}
            {pairs.length - both.length ? `; ${pairs.length - both.length} in only one` : ""}.
            {gaps.length > 0 && ` Largest gap ${Math.max(...gaps).toFixed(2)} s.`}
          </p>
          <ol className="text-sm leading-6">
            {pairs.map((p, n) => {
              const gap = timed && p.a && p.b ? p.b.t - p.a.t : 0;
              return (
                <li key={n} className={cn("grid grid-cols-[3.5rem_1fr_auto] items-baseline gap-2 rounded px-1.5",
                                          !p.b && "bg-sky-400/10", !p.a && "bg-amber-400/10")}>
                  <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
                    {(p.a && one.data?.synced) || (!p.a && two.data?.synced) ? clock((p.a ?? p.b)!.t).replace(/\.\d+$/, "") : ""}
                  </span>
                  <span className={cn(!(p.a ?? p.b)!.s && "text-muted-foreground/50")}>{(p.a ?? p.b)!.s || "♪"}</span>
                  <span className="text-[11px] tabular-nums">
                    {!p.b ? <span className="text-sky-300">only first</span> : !p.a ? <span className="text-amber-300">only second</span>
                      : Math.abs(gap) >= 0.3 ? <span className="text-warning">{gap > 0 ? "+" : ""}{gap.toFixed(2)} s</span> : ""}
                  </span>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </div>
  );
}

/** An action, and when it can't be done now, why, said under it (not only in a tooltip). */
function Action({ children, reason }: { children: ReactNode; reason?: string | null }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 sm:max-w-56 [&>button]:w-full [&>a]:w-full sm:[&>button]:w-auto sm:[&>a]:w-auto">
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
                <span className="text-xs text-muted-foreground" title="Settings, General: the quality songs are searched until">
                  {reached ? "Good enough: not searched again" : `Searched again until ${LEVELS[quality.cutoff].toLowerCase()}`}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="grid w-full grid-cols-2 items-start gap-2 sm:flex sm:w-auto sm:flex-wrap sm:gap-3">
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
            <Tabs defaultValue={String(files[0].id)}>
              <TabsList className="mb-3 flex h-auto w-full justify-start overflow-x-auto">
                {files.map((file) => (
                  <TabsTrigger key={file.id} value={String(file.id)} title={file.name} className="shrink-0 flex-none">{versionName(file)}</TabsTrigger>
                ))}
                {files.length > 1 && <TabsTrigger value="compare" className="shrink-0 flex-none">Compare</TabsTrigger>}
              </TabsList>
              {files.map((file) => (
                <TabsContent key={file.id} value={String(file.id)}>
                  {file.source === "ai" && best && best.lines.some((l) => l.u && !l.fixed)
                    ? <Lines lines={best.lines} synced={best.origin !== "plain"} />
                    : <Version songId={id} file={file} />}
                </TabsContent>
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
                            {file.source === "unknown" ? "Maker not recorded" : `By ${SOURCE[file.source] ?? file.source}`}
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
