import { ArrowLeft, Download, ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { Address } from "@/components/common/Address";
import { CopyButton } from "@/components/common/CopyButton";
import { Size } from "@/components/common/Size";
import { JobActions } from "@/components/jobs/JobActions";
import { JobProgress } from "@/components/jobs/JobProgress";
import { Thumbnail } from "@/components/jobs/JobRow";
import { CODEC, CONTAINER, jobTitle, QUALITY, sectionText, STATUS } from "@/components/jobs/labels";
import { VideoFile } from "@/components/jobs/VideoFile";
import { Failed, Loading } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { fileUrl, type Job } from "@/lib/api";
import { useJob } from "@/lib/queries";
import { clock, timeAgo } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-4 border-b border-border py-2.5 text-sm last:border-0">
      <dt className="w-16 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 flex-1 justify-end text-right break-words">{children}</dd>
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
  const videos = job.files.filter((file) => file.kind === "video");
  const others = job.files.filter((file) => file.kind !== "video");

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
            {job.section && <Badge variant="muted">part {sectionText(job.section)}</Badge>}
          </div>
          <h1 className="text-xl font-semibold tracking-tight break-words sm:text-2xl">{jobTitle(job)}</h1>
          {job.uploader && <p className="text-sm text-muted-foreground">{job.uploader}</p>}
          <JobProgress job={job} className="mt-1" />
          {job.error && <p className={job.status === "finished" ? "text-sm text-warning" : "text-sm text-destructive"}>{job.error}</p>}
          {job.notes.map((note, n) => <p key={n} className="text-sm text-muted-foreground">{note}</p>)}
          <JobActions job={job} className="mt-auto pt-2" />
        </div>
      </div>

      {videos.map((file) => <VideoFile key={file.index} job={job} file={file} />)}

      {others.length > 0 && (
        <Card className="mb-4 p-4">
          <p className="mb-1 font-medium">Other files</p>
          <ul className="flex flex-col divide-y divide-border">
            {others.map((file) => (
              <li key={file.index} className="flex items-center gap-3 py-2">
                <p className="min-w-0 flex-1 truncate text-sm" title={file.name}>{file.name}</p>
                <span className="text-xs text-muted-foreground"><Size bytes={file.size} /></span>
                {file.available && (
                  <Button variant="ghost" size="icon-sm" asChild title="Download it">
                    <a href={fileUrl(job, file)} download aria-label="Download it"><Download /></a>
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <dl>
            <Row label="Link">
              <div className="flex min-w-0 items-center gap-0.5">
                <a href={job.url} target="_blank" rel="noreferrer" title="Open it on its site"
                   className="scroll-thin min-w-0 overflow-x-auto py-1 whitespace-nowrap text-sky-300 hover:underline">{job.url}</a>
                <CopyButton text={job.url} label="link" />
                <Button variant="ghost" size="icon-sm" asChild>
                  <a href={job.url} target="_blank" rel="noreferrer" aria-label="Open it on its site" title="Open it on its site"><ExternalLink /></a>
                </Button>
              </div>
            </Row>
            <Row label="Into">{job.destination ? `${job.destination} (${job.folder})` : job.folder ?? "kept here, to download"}</Row>
            <Row label="Options">{options(job)}</Row>
            {job.section && <Row label="Part">{sectionText(job.section)} ({clock(job.section.end - job.section.start)})</Row>}
            {job.song && <Row label="Song">{job.song.artist} – {job.song.title}</Row>}
            {job.reused_from && <Row label="File"><span>reused from <Link to={`/jobs/${job.reused_from}`} className="text-sky-300 hover:underline">an earlier download</Link></span></Row>}
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
