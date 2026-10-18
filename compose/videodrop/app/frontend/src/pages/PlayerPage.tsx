import { ArrowLeft, Download, Film } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router";

import { Size } from "@/components/common/Size";
import { jobTitle, sectionText } from "@/components/jobs/labels";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { Player } from "@/components/player/Player";
import { Button } from "@/components/ui/button";
import { fileUrl, local } from "@/lib/api";
import { useFileDetails, useJob } from "@/lib/queries";

/** A download's video, big: /jobs/<id>/play (?file=<n> for another of its files, &t=<seconds> to start there). */
export default function PlayerPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const { data: job, error, isPending, refetch } = useJob(id);
  const videos = job?.files.filter((file) => file.kind === "video" && file.available) ?? [];
  const file = videos.find((f) => f.index === Number(params.get("file") ?? videos[0]?.index)) ?? videos[0];
  const { data: details } = useFileDetails(id, file?.index ?? 0, !!file);
  const start = params.get("t") ? Number(params.get("t")) : undefined;

  if (isPending) return <Loading />;
  if (error || !job) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <Link to={`/jobs/${job.id}`} className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> The download
      </Link>
      {!file ? (
        <Empty icon={<Film />} title="Nothing to play">
          {job.status === "finished" ? "Its file isn't here any more." : "It isn't downloaded yet."}
        </Empty>
      ) : (
        <>
          <Player key={`${job.id}.${file.index}`} src={fileUrl(job, file, true)} details={details} name={file.name} autoPlay
                  poster={details?.frames[0] ? local(details.frames[0].url) : job.thumbnail ?? undefined}
                  remember={`videodrop.position.${job.id}.${file.index}`} startAt={Number.isFinite(start) ? start : undefined}
                  className="rounded-xl shadow-2xl shadow-black/50 ring-1 ring-border" />
          <div className="mt-4 flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-semibold tracking-tight break-words sm:text-xl">{jobTitle(job)}</h1>
              <p className="text-sm text-muted-foreground">
                {[job.uploader, job.section && `part ${sectionText(job.section)}`].filter(Boolean).join(" · ")}
                {job.uploader || job.section ? " · " : ""}<Size bytes={file.size} />
              </p>
            </div>
            <Button variant="secondary" size="sm" asChild>
              <a href={fileUrl(job, file)} download><Download /> Download</a>
            </Button>
          </div>
          {videos.length > 1 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {videos.map((v) => (
                <Button key={v.index} variant={v.index === file.index ? "secondary" : "ghost"} size="sm" asChild>
                  <Link to={`?file=${v.index}`} replace>{v.name}</Link>
                </Button>
              ))}
            </div>
          )}
          <p className="mt-6 text-xs text-muted-foreground">
            Keys: space or k play · j / l 10 s · ← → 5 s · ↑ ↓ volume · m mute · f full screen · c subtitles · &lt; &gt; speed · , . a frame · 0–9 jump · i details
          </p>
        </>
      )}
    </>
  );
}
