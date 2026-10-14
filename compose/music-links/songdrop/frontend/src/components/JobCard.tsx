import {
  AlertTriangle,
  Ban,
  Check,
  Clock,
  Disc3,
  Download,
  FolderDown,
  ListMusic,
  Loader2,
  Music2,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ACTIVE, api, ApiError, type Job } from "@/lib/api";
import { cn, formatBytes, timeAgo, timeLeft } from "@/lib/utils";

import { engineVariant, kindLabel } from "./labels";

const STATUS = {
  queued: { label: "Queued", variant: "muted", icon: Clock },
  running: { label: "Downloading", variant: "site", icon: Loader2 },
  done: { label: "Done", variant: "success", icon: Check },
  partial: { label: "Partly done", variant: "warning", icon: AlertTriangle },
  failed: { label: "Failed", variant: "destructive", icon: AlertTriangle },
  cancelled: { label: "Cancelled", variant: "muted", icon: Ban },
} as const;

const KIND_ICON = { album: Disc3, playlist: ListMusic, artist: User } as Record<string, typeof Music2>;

export function JobCard({ job, onChange }: { job: Job; onChange: () => void }) {
  const [acting, setActing] = useState(false);
  const status = STATUS[job.status];
  const StatusIcon = status.icon;
  const KindIcon = KIND_ICON[job.kind] ?? Music2;
  const active = ACTIVE.includes(job.status);
  const notDelivered = job.files.filter((file) => !file.delivered).length;

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
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/25 to-fuchsia-500/20 ring-1 ring-white/10">
          <KindIcon className="size-5 text-violet-200" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium leading-snug" title={job.title ?? job.url}>
            {job.title ?? job.url}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <Badge variant={engineVariant(job.engine, job.site)}>{job.site}</Badge>
            <span>{kindLabel(job.kind)}</span>
            <span aria-hidden>·</span>
            <span>{timeAgo(job.created_at)}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Badge variant={status.variant} className="hidden sm:inline-flex">
            <StatusIcon className={cn(job.status === "running" && "animate-spin")} /> {status.label}
          </Badge>
          {active ? (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Cancel"
              title="Cancel"
              disabled={acting}
              onClick={() => act(() => api.cancel(job.id), "Couldn't cancel")}
            >
              <X />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Remove from the list"
              title="Remove from the list (the library keeps the songs)"
              disabled={acting}
              onClick={() => act(() => api.remove(job.id), "Couldn't remove it")}
            >
              <Trash2 />
            </Button>
          )}
        </div>
      </div>

      <Badge variant={status.variant} className="mt-3 sm:hidden">
        <StatusIcon className={cn(job.status === "running" && "animate-spin")} /> {status.label}
      </Badge>

      {job.status === "running" && (
        <div className="mt-4">
          <Progress value={Math.round((job.progress ?? 0) * 100)} />
          <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span className="truncate">{job.current ?? (job.total ? "Working…" : "Reading the link…")}</span>
            {job.total > 1 && (
              <span className="shrink-0 tabular-nums">
                {job.completed} / {job.total}
              </span>
            )}
          </div>
        </div>
      )}

      {job.status === "queued" && (
        <p className="mt-3 text-sm text-muted-foreground">Waiting for the downloads before it to finish.</p>
      )}

      {job.message && job.status !== "cancelled" && (
        <p className={cn("mt-3 text-sm", job.status === "failed" ? "text-destructive" : "text-muted-foreground")}>
          {job.message}
        </p>
      )}

      {job.files.length > 0 && (
        <ul className="mt-4 divide-y divide-border overflow-hidden rounded-lg border border-border bg-black/15">
          {job.files.map((file) => (
            <li key={file.id} className="flex items-center gap-3 px-3 py-2.5">
              <Music2 className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{file.title ?? file.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {[file.artist, formatBytes(file.size)].filter(Boolean).join(" · ")}
                  {!file.delivered && <span className="text-warning"> · not in the library</span>}
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

      {job.errors.length > 0 && (
        <details className="group mt-3">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-sm text-warning marker:hidden">
            <AlertTriangle className="size-4" />
            {job.errors.length} {job.errors.length === 1 ? "song" : "songs"} couldn't be downloaded
          </summary>
          <ul className="mt-2 space-y-2 border-l border-warning/30 pl-3">
            {job.errors.map((error, index) => (
              <li key={index} className="text-xs">
                <p className="font-medium text-foreground/90">{error.name}</p>
                <p className="text-muted-foreground">{error.message}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {!active && job.files.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>
            {notDelivered === job.files.length ? "Not added to the library" : "Sent to your library"}
            {job.expires_at && <> · links expire in {timeLeft(job.expires_at)}</>}
          </span>
          {job.archive_url && (
            <Button asChild variant="secondary" size="sm">
              <a href={job.archive_url} download>
                <FolderDown /> Download all
              </a>
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
