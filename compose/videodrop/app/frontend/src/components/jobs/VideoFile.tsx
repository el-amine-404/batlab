import { FileX2, RotateCcw, ShieldAlert, ShieldCheck } from "lucide-react";
import { type ReactNode, useRef } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/common/CopyButton";
import { ScrollingText } from "@/components/common/ScrollingText";
import { Size } from "@/components/common/Size";
import { Chapters } from "@/components/player/Chapters";
import { definition, audioName, videoName } from "@/components/player/names";
import { Player, type PlayerHandle } from "@/components/player/Player";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { api, errorText, fileUrl, type Job, type JobFile, local } from "@/lib/api";
import { useFileDetails, useJobsChanged } from "@/lib/queries";
import { clock, cn, formatBitrate, formatWhen } from "@/lib/utils";

import { Expiry } from "./Expiry";

function Fact({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-[11px] tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className="text-sm break-words tabular-nums">{children}</dd>
    </div>
  );
}

/** A file that isn't there any more: when and why, and the way to have it again. */
function Gone({ job, file }: { job: Job; file: JobFile }) {
  const changed = useJobsChanged();
  const again = async () => {
    try {
      await api.retryJob(job.id);
      toast.success("Queued again");
    } catch (caught) {
      toast.error(errorText(caught));
    }
    changed();
  };
  const all = job.status === "finished" && job.files.every((f) => !f.available);
  return (
    <Card className="mb-4 flex flex-wrap items-center gap-3 p-4 text-sm text-muted-foreground">
      <FileX2 className="size-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-foreground" title={file.name}>{file.name}</p>
        <p>
          {file.removed_at
            ? `Removed ${formatWhen(file.removed_at)}: a download without a destination is kept ${job.hold_hours} h.`
            : "Not in its folder any more: it was moved or deleted there."}
          {" "}Its link now answers “410 Gone”.
        </p>
      </div>
      {all && <Button size="sm" onClick={() => void again()}><RotateCcw /> Download again</Button>}
    </Card>
  );
}

/**
 * A downloaded video: a small player to see it, what it is (read from the
 * file itself), where it is and, kept here, until when; its check, its
 * chapters, and pictures across it (at its chapters when it has some); a
 * chapter or a picture plays the video from there.
 */
export function VideoFile({ job, file }: { job: Job; file: JobFile }) {
  const player = useRef<PlayerHandle>(null);
  const { data: details, isPending, error } = useFileDetails(job.id, file.index, file.available);

  if (!file.available) return <Gone job={job} file={file} />;

  const v = details?.video;
  const audio = details?.audio[0];
  const where = file.path ? `media/${file.path}` : null;
  const chapters = details?.chapters.filter((c) => c.end > c.start) ?? [];
  const frames = details?.frames ?? [];
  const seek = (time: number) => player.current?.seek(time, true);

  return (
    <Card className="mb-4 overflow-hidden p-0">
      <div className="grid md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Player ref={player} compact src={fileUrl(job, file, true)} details={details} name={file.name}
                poster={details?.frames[0] ? local(details.frames[0].url) : job.thumbnail ?? undefined}
                remember={`videodrop.position.${job.id}.${file.index}`} />
        <div className="flex min-w-0 flex-col gap-3 p-4">
          <div className="flex items-start gap-1">
            <p className="min-w-0 flex-1 text-sm font-medium break-all">{file.name}</p>
            <CopyButton text={file.name} label="file name" />
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
            <Fact label="Size"><Size bytes={file.size} /></Fact>
            <Fact label="Length">{details?.duration ? clock(details.duration) : "–"}</Fact>
            <Fact label="Picture">{v ? [definition(v.width, v.height), v.fps ? `${Math.round(v.fps)} fps` : null].filter(Boolean).join(" · ") : "–"}</Fact>
            <Fact label="Video">{v ? `${videoName(v.codec)}${v.kbps ? ` · ${formatBitrate(v.kbps)}` : ""}` : "–"}</Fact>
            <Fact label="Sound">{audio ? `${audioName(audio.codec)}${audio.kbps ? ` · ${formatBitrate(audio.kbps)}` : ""}` : details ? "none" : "–"}</Fact>
            <Fact label="Bitrate">{details?.kbps ? formatBitrate(details.kbps) : "–"}</Fact>
            {v && <Fact label="Resolution">{v.width}×{v.height}{v.dynamic_range !== "SDR" ? ` · ${v.dynamic_range}` : ""}</Fact>}
            {!!details?.subtitles.length && <Fact label="Subtitles">{details.subtitles.map((s) => s.title ?? s.language ?? s.codec).join(", ")}</Fact>}
            {details && (
              <Fact label="Chapters">
                {chapters.length || (
                  <Tooltip content="The site gave none for this video. When it does, they are in the file, on the seek bar, and the gallery follows them.">
                    <span tabIndex={0} className="cursor-help text-muted-foreground underline decoration-dotted underline-offset-4">none</span>
                  </Tooltip>
                )}
              </Fact>
            )}
          </dl>
          {error && <p className="text-xs text-warning">The file couldn't be read for its details.</p>}
          <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            {where ? (
              <>
                <ScrollingText text={where} className="flex-1 font-mono" />
                <CopyButton text={where} label="file's place" />
              </>
            ) : (
              <span className="flex flex-wrap items-center gap-x-1.5">
                Kept here for whoever asked{file.expires_at ? <>, <Expiry job={job} at={file.expires_at} /></> : "."}
              </span>
            )}
          </div>
          {file.existed && <p className="text-xs text-muted-foreground">{job.reused_from ? "The earlier download's file, reused." : "It was there already, and was kept as it was."}</p>}
          {file.checks && (
            <p className={cn("flex items-start gap-1.5 text-xs", file.checks.ok ? "text-success" : "text-destructive")}
               title="Read by ffprobe after the download: a real video, of the length the site said, in the container its name says">
              {file.checks.ok ? <ShieldCheck className="size-3.5 shrink-0" /> : <ShieldAlert className="size-3.5 shrink-0" />}
              <span>Checked: {file.checks.summary}
                {file.checks.problems.length > 0 && <span className="text-warning"> · {file.checks.problems.join(" ")}</span>}
              </span>
            </p>
          )}
          {chapters.length > 0 && (
            <details className="group -mx-2" open={chapters.length <= 8}>
              <summary className="cursor-pointer list-none px-2 text-[11px] tracking-wide text-muted-foreground uppercase hover:text-foreground">
                Chapters · {chapters.length}
              </summary>
              <Chapters chapters={chapters} onSeek={seek} className="mt-1" />
            </details>
          )}
        </div>
      </div>
      {(isPending || frames.length > 0) && (
        <div className={cn("grid gap-1.5 border-t border-border p-2", frames.length && frames.length <= 4 ? "grid-cols-4" : "grid-cols-4 sm:grid-cols-8")}>
          {(isPending ? Array.from({ length: 8 }, () => null) : frames).map((frame, n) => (
            frame ? (
              <button key={n} type="button" onClick={() => seek(frame.time)} title={`Play from ${clock(frame.time)}${frame.chapter ? `: ${frame.chapter}` : ""}`}
                      className="group relative aspect-video overflow-hidden rounded-md bg-secondary outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <img src={local(frame.url)} alt={`At ${clock(frame.time)}`} loading="lazy" className="size-full object-cover transition-transform group-hover:scale-105" />
                {frame.chapter && (
                  <span className="absolute inset-x-0 top-0 truncate bg-gradient-to-b from-black/80 to-transparent px-1 pt-0.5 pb-2 text-left text-[10px] font-medium">{frame.chapter}</span>
                )}
                <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 text-[10px] font-medium tabular-nums">{clock(frame.time)}</span>
              </button>
            ) : <div key={n} className="skeleton aspect-video rounded-md" />
          ))}
        </div>
      )}
    </Card>
  );
}
