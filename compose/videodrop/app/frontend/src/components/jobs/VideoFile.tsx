import { FileX2, ShieldAlert, ShieldCheck } from "lucide-react";
import { type ReactNode, useRef } from "react";

import { CopyButton } from "@/components/common/CopyButton";
import { Size } from "@/components/common/Size";
import { definition, audioName, videoName } from "@/components/player/names";
import { Player, type PlayerHandle } from "@/components/player/Player";
import { Card } from "@/components/ui/card";
import { fileUrl, type Job, type JobFile, local } from "@/lib/api";
import { useFileDetails, useSettings } from "@/lib/queries";
import { clock, cn, formatBitrate } from "@/lib/utils";

function Fact({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-[11px] tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className="text-sm break-words tabular-nums">{children}</dd>
    </div>
  );
}

/**
 * A downloaded video: a small player to see it, what it is (read from the
 * file itself), where it is, its check, and pictures taken across it; a
 * picture plays the video from there.
 */
export function VideoFile({ job, file }: { job: Job; file: JobFile }) {
  const player = useRef<PlayerHandle>(null);
  const { data: details, isPending, error } = useFileDetails(job.id, file.index, file.available);
  const { data: settings } = useSettings();

  if (!file.available) {
    return (
      <Card className="mb-4 flex items-center gap-3 p-4 text-sm text-muted-foreground">
        <FileX2 className="size-5 shrink-0" />
        <div className="min-w-0">
          <p className="truncate text-foreground">{file.name}</p>
          <p>Not there any more{file.path ? "" : `: a file kept here goes after ${settings?.hold_hours ?? 24} h`}.</p>
        </div>
      </Card>
    );
  }

  const v = details?.video;
  const audio = details?.audio[0];
  const until = !file.path && job.finished_at && settings
    ? new Date((job.finished_at + settings.hold_hours * 3600) * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : null;
  const where = file.path ? `media/${file.path}` : null;

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
            {!!details?.chapters.length && <Fact label="Chapters">{details.chapters.length}</Fact>}
          </dl>
          {error && <p className="text-xs text-warning">The file couldn't be read for its details.</p>}
          <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            {where ? (
              <>
                <code className="scroll-thin min-w-0 flex-1 overflow-x-auto whitespace-nowrap">{where}</code>
                <CopyButton text={where} label="file's place" />
              </>
            ) : (
              <span>Kept here for its caller{until ? `, until ${until}` : ""}.</span>
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
        </div>
      </div>
      {(isPending || !!details?.frames.length) && (
        <div className="grid grid-cols-4 gap-1.5 border-t border-border p-2 sm:grid-cols-8">
          {(details?.frames ?? Array.from({ length: 8 }, () => null)).map((frame, n) => (
            frame ? (
              <button key={n} type="button" onClick={() => player.current?.seek(frame.time, true)} title={`Play from ${clock(frame.time)}`}
                      className="group relative aspect-video overflow-hidden rounded-md bg-secondary outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <img src={local(frame.url)} alt={`At ${clock(frame.time)}`} loading="lazy" className="size-full object-cover transition-transform group-hover:scale-105" />
                <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 text-[10px] font-medium tabular-nums">{clock(frame.time)}</span>
              </button>
            ) : <div key={n} className="skeleton aspect-video rounded-md" />
          ))}
        </div>
      )}
    </Card>
  );
}
