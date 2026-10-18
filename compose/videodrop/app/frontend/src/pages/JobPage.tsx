import { ArrowLeft, Download, Play, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { Address } from "@/components/common/Address";
import { JobActions } from "@/components/jobs/JobActions";
import { JobProgress } from "@/components/jobs/JobProgress";
import { CODEC, CONTAINER, jobTitle, QUALITY, STATUS } from "@/components/jobs/labels";
import { Thumbnail } from "@/components/jobs/JobRow";
import { Failed, Loading } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { fileUrl, type Job } from "@/lib/api";
import { useJob } from "@/lib/queries";
import { formatBytes, timeAgo } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-border py-2.5 text-sm last:border-0">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right break-words">{children}</dd>
    </div>
  );
}

function options(job: Job): string {
  const o = job.options;
  return [
    QUALITY[o.quality],
    o.codec !== "any" && CODEC[o.codec],
    CONTAINER[o.container],
    o.subtitles.length ? `subtitles ${o.subtitles.join(", ")}` : null,
    o.lyrics ? "lyrics" : null,
    o.playlist ? `playlist (${o.playlist_limit} at most)` : null,
  ].filter(Boolean).join(" · ");
}

export function JobPage() {
  const { id = "" } = useParams();
  const { data: job, error, isPending, refetch } = useJob(id);
  if (isPending) return <Loading />;
  if (error || !job) return <Failed error={error} retry={() => void refetch()} />;
  const status = STATUS[job.status];
  const api = `${window.location.origin}/api/v1/jobs/${job.id}`;

  return (
    <>
      <Link to="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Downloads
      </Link>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row">
        <Thumbnail job={job} className="aspect-video w-full sm:w-64" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={status.variant}>{status.label}</Badge>
            {job.client && <Badge variant="muted">by {job.client}</Badge>}
          </div>
          <h1 className="text-xl font-semibold tracking-tight break-words sm:text-2xl">{jobTitle(job)}</h1>
          {job.uploader && <p className="text-sm text-muted-foreground">{job.uploader}</p>}
          <JobProgress job={job} className="mt-1" />
          {job.error && <p className={job.status === "finished" ? "text-sm text-warning" : "text-sm text-destructive"}>{job.error}</p>}
          {job.notes.map((note, n) => <p key={n} className="text-sm text-muted-foreground">{note}</p>)}
          <div className="mt-auto pt-2"><JobActions job={job} /></div>
        </div>
      </div>

      {job.files.length > 0 && (
        <Card className="mb-4 p-4">
          <p className="mb-2 font-medium">Files</p>
          <ul className="flex flex-col divide-y divide-border">
            {job.files.map((file) => (
              <li key={file.index} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm" title={file.name}>{file.name}</p>
                  <p className="truncate text-xs text-muted-foreground" title={file.path ?? undefined}>
                    {formatBytes(file.size)} · {file.path ? `media/${file.path}` : "held here for its caller"}
                    {file.existed && " · was there already, kept as it was"}
                    {!file.available && " · not there any more"}
                  </p>
                  {file.checks && (
                    <p className={file.checks.ok ? "flex items-center gap-1 text-xs text-success" : "flex items-center gap-1 text-xs text-destructive"}
                       title="Read by ffprobe after the download: a real video, of the length the site said, in the container its name says">
                      <ShieldCheck className="size-3.5" /> Checked: {file.checks.summary}
                      {file.checks.problems.length > 0 && <span className="text-warning"> · {file.checks.problems.join(" ")}</span>}
                    </p>
                  )}
                </div>
                {file.available && (
                  <>
                    {file.kind === "video" && (
                      <Button variant="ghost" size="icon-sm" asChild title="Play it">
                        <a href={fileUrl(job, file, true)} target="_blank" rel="noreferrer" aria-label="Play it"><Play /></a>
                      </Button>
                    )}
                    <Button variant="ghost" size="icon-sm" asChild title="Download it">
                      <a href={fileUrl(job, file)} download aria-label="Download it"><Download /></a>
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <dl>
            <Row label="Link"><a href={job.url} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">{job.url}</a></Row>
            <Row label="Into">{job.destination ? `${job.destination} (${job.folder})` : job.folder ?? "kept here, to download"}</Row>
            <Row label="Options">{options(job)}</Row>
            {job.song && <Row label="Song">{job.song.artist} – {job.song.title}</Row>}
            <Row label="Asked">{timeAgo(job.created_at)}{job.priority ? ` · priority ${job.priority}` : ""}</Row>
            {job.started_at && <Row label="Started">{timeAgo(job.started_at)}{job.attempts > 1 ? ` · try ${job.attempts}` : ""}</Row>}
            {job.finished_at && <Row label="Ended">{timeAgo(job.finished_at)}</Row>}
          </dl>
        </Card>
        <Card className="flex flex-col gap-3 p-4">
          <div>
            <p className="font-medium">For programs</p>
            <p className="text-xs text-muted-foreground">Follow this download from another app.</p>
          </div>
          <Address label="Job" hint="GET, JSON" url={api} />
          <Address label="Live" hint="Server-Sent Events" url={`${api}/events`} />
          {job.callback && (
            <p className="text-xs text-muted-foreground">
              Callback to <code>{job.callback.url}</code>{job.callback.signed && ", signed"}
              {job.callback.last && (
                <span className={job.callback.last.ok ? "text-success" : "text-destructive"}>
                  {" "}· {job.callback.last.event} {job.callback.last.ok ? "delivered" : `failed: ${job.callback.last.message}`}
                </span>
              )}
            </p>
          )}
        </Card>
      </div>
    </>
  );
}
