import {
  AlertTriangle,
  Ban,
  Check,
  ChevronDown,
  Clock,
  Disc3,
  Download,
  ExternalLink,
  FolderDown,
  Info,
  ListMusic,
  Loader2,
  Music2,
  RotateCw,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ACTIVE, api, ApiError, RETRYING, type Job, type JobError } from "@/lib/api";
import { cn, formatBytes, timeAgo, timeLeft } from "@/lib/utils";

import { engineVariant, kindLabel } from "./labels";

const STATUS = {
  queued: { label: "Queued", variant: "muted", icon: Clock },
  running: { label: "Downloading", variant: "site", icon: Loader2 },
  done: { label: "Done", variant: "success", icon: Check },
  partial: { label: "Songs missing", variant: "warning", icon: AlertTriangle },
  failed: { label: "Failed", variant: "destructive", icon: AlertTriangle },
  cancelled: { label: "Cancelled", variant: "muted", icon: Ban },
} as const;

const KIND_ICON = { album: Disc3, playlist: ListMusic, artist: User } as Record<string, typeof Music2>;

function songCount(job: Job): string {
  if (job.status === "queued") return "waiting to start";
  if (job.total > 1) return `${job.completed} of ${job.total} songs`;
  if (job.status === "running") return job.total ? "downloading" : "reading the link";
  return job.completed ? "1 song" : "no song";
}

/** One download: a line with its status; opened, its songs and what is missing. */
export function JobRow({ job, onChange }: { job: Job; onChange: () => void }) {
  const active = ACTIVE.includes(job.status);
  const [open, setOpen] = useState(active);
  const [acting, setActing] = useState(false);
  const status = STATUS[job.status];
  const StatusIcon = status.icon;
  const KindIcon = KIND_ICON[job.kind] ?? Music2;
  const retrying = job.errors.some((error) => RETRYING.includes(error.state));

  const act = async (action: () => Promise<unknown>, failure: string) => {
    setActing(true);
    try {
      await action();
      onChange();
    } catch (caught) {
      toast.error(failure, { description: caught instanceof ApiError ? caught.message : undefined });
    } finally {
      setActing(false);
    }
  };

  return (
    <article className={cn("transition-colors", open && "bg-white/[0.025]")}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.03]"
      >
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/25 to-fuchsia-500/20 ring-1 ring-white/10">
          <KindIcon className="size-4 text-violet-200" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" title={job.title ?? job.url}>
            {job.title ?? job.url}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-muted-foreground">
            <span className={cn("size-1.5 shrink-0 rounded-full", dot(engineVariant(job.engine, job.site)))} />
            <span className="truncate">
              {job.site} {kindLabel(job.kind)} · {songCount(job)} · {timeAgo(job.created_at)}
            </span>
          </p>
        </div>
        <Badge variant={status.variant} className="shrink-0">
          <StatusIcon className={cn(job.status === "running" && "animate-spin")} />
          <span className="hidden sm:inline">{retrying ? "Trying again" : status.label}</span>
        </Badge>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>

      {job.status === "running" && (
        <div className="px-4 pb-3">
          <Progress value={Math.round((job.progress ?? 0) * 100)} className="h-1" />
          <p className="mt-1.5 truncate text-xs text-muted-foreground">
            {job.current ?? (job.total ? "Working…" : "Reading the link…")}
          </p>
        </div>
      )}

      {open && (
        <div className="flex flex-col gap-3 px-4 pb-4">
          {job.message && job.status !== "cancelled" && (
            <p className={cn("text-sm", job.status === "failed" ? "text-destructive" : "text-muted-foreground")}>
              {job.message}
            </p>
          )}

          {job.errors.length > 0 && (
            <section className="rounded-lg border border-warning/30 bg-warning/5 p-3">
              <p className="flex items-center gap-1.5 text-sm font-medium text-warning">
                <AlertTriangle className="size-4 shrink-0" />
                {missingHeadline(job.errors)}
              </p>
              <ul className="mt-2 divide-y divide-warning/15">
                {job.errors.map((error, position) => (
                  <MissingSong
                    key={error.index ?? `at-${position}`}
                    error={error}
                    canRetry={!active && !acting}
                    onRetry={() =>
                      error.index !== null && act(() => api.retry(job.id, error.index as number), "Couldn't try again")
                    }
                  />
                ))}
              </ul>
            </section>
          )}

          {job.files.length > 0 && (
            <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-black/15">
              {job.files.map((file) => (
                <li key={file.id} className="flex items-center gap-3 px-3 py-2">
                  <Music2 className="size-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">{file.title ?? file.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {[file.artist, formatBytes(file.size)].filter(Boolean).join(" · ")}
                      {!file.delivered && <span className="text-warning"> · not in the output folder</span>}
                    </p>
                  </div>
                  <Button asChild variant="ghost" size="icon-sm">
                    <a href={file.url} download={file.name} aria-label={`Download ${file.name}`} title="Download">
                      <Download />
                    </a>
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {job.notes.length > 0 && (
            <ul className="space-y-1">
              {job.notes.map((note, position) => (
                <li key={position} className="flex gap-1.5 text-xs text-muted-foreground">
                  <Info className="mt-0.5 size-3.5 shrink-0" />
                  <span>
                    <span className="text-foreground/80">{note.name}</span>: {note.message}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <a href={job.url} target="_blank" rel="noreferrer noopener" className="flex min-w-0 items-center gap-1 hover:text-foreground">
              <ExternalLink className="size-3.5 shrink-0" /> <span className="truncate">Open the link</span>
            </a>
            <div className="flex items-center gap-1">
              {!active && job.expires_at && <span className="mr-2">Kept {timeLeft(job.expires_at)} more</span>}
              {job.archive_url && (
                <Button asChild variant="secondary" size="sm">
                  <a href={job.archive_url} download>
                    <FolderDown /> Download all
                  </a>
                </Button>
              )}
              {active ? (
                <Button variant="ghost" size="sm" disabled={acting} onClick={() => act(() => api.cancel(job.id), "Couldn't cancel")}>
                  <X /> Cancel
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={acting}
                  title="Remove from the list; songs already sent to your library stay"
                  onClick={() => act(() => api.remove(job.id), "Couldn't remove it")}
                >
                  <Trash2 /> Remove
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

function dot(variant: "spotify" | "youtube" | "site"): string {
  return { spotify: "bg-spotify", youtube: "bg-youtube", site: "bg-primary" }[variant];
}

function missingHeadline(errors: JobError[]): string {
  const count = errors.length === 1 ? "1 song is" : `${errors.length} songs are`;
  const later = errors.some((error) => error.state === "waiting" || RETRYING.includes(error.state));
  return later ? `${count} still missing. songdrop keeps trying on its own.` : `${count} missing.`;
}

function MissingSong({ error, canRetry, onRetry }: { error: JobError; canRetry: boolean; onRetry: () => void }) {
  const busy = RETRYING.includes(error.state);
  const spotify = error.url?.startsWith("https://open.spotify.com/") ? error.url : null;
  return (
    <li className="flex flex-col gap-2 py-2.5 first:pt-1 last:pb-0 sm:flex-row sm:items-center sm:gap-3">
      <div className="min-w-0 flex-1 text-xs">
        <p className="truncate text-sm font-medium text-foreground/90">{error.name}</p>
        <p className="text-muted-foreground">{error.message}</p>
        <p className="mt-1 flex items-center gap-1.5 text-foreground/70">
          {busy && (
            <>
              <Loader2 className="size-3.5 animate-spin" /> Trying again now…
            </>
          )}
          {error.state === "waiting" && error.next_retry_at && (
            <>
              <Clock className="size-3.5" /> Next try in {timeLeft(error.next_retry_at)} (try {error.tries + 1} of{" "}
              {error.max_tries})
            </>
          )}
          {error.state === "gave_up" && <>No more tries on its own.</>}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {spotify && (
          <Button asChild variant="ghost" size="sm">
            <a href={spotify} target="_blank" rel="noreferrer noopener" title="Open this song on Spotify">
              <ExternalLink /> Spotify
            </a>
          </Button>
        )}
        {error.url && (error.state === "waiting" || error.state === "gave_up") && (
          <Button variant="secondary" size="sm" disabled={!canRetry} onClick={onRetry}>
            <RotateCw /> Retry now
          </Button>
        )}
      </div>
    </li>
  );
}
