import { Film } from "lucide-react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import type { Job } from "@/lib/api";
import { clock, timeAgo } from "@/lib/utils";

import { JobActions } from "./JobActions";
import { JobProgress } from "./JobProgress";
import { jobTitle, sectionText, STATUS } from "./labels";

export function Thumbnail({ job, className = "h-16 w-28" }: { job: Job; className?: string }) {
  return (
    <div className={`relative shrink-0 overflow-hidden rounded-lg bg-secondary ${className}`}>
      {job.thumbnail ? (
        <img src={job.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-full object-cover" />
      ) : (
        <Film className="absolute inset-0 m-auto size-5 text-muted-foreground" />
      )}
      {job.duration ? (
        <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 text-[10px] font-medium tabular-nums">{clock(job.duration)}</span>
      ) : null}
    </div>
  );
}

/**
 * One download in the list: what it is, where it's going, how it's doing;
 * on the right edge its status, and under it what can be done with it.
 */
export function JobRow({ job }: { job: Job }) {
  const status = STATUS[job.status];
  const where = job.destination ?? (job.folder ? job.folder : "held for its caller");
  return (
    <li className="flex gap-3 rounded-xl border border-border bg-card p-3 sm:gap-4">
      <Link to={`/jobs/${job.id}`} className="shrink-0" tabIndex={-1} aria-hidden>
        <Thumbnail job={job} className="h-14 w-24 sm:h-16 sm:w-28" />
      </Link>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <Link to={`/jobs/${job.id}`} className="truncate font-medium hover:underline" title={jobTitle(job)}>
          {jobTitle(job)}
        </Link>
        <p className="truncate text-xs text-muted-foreground">
          {[job.uploader, job.site, job.section && `part ${sectionText(job.section)}`, where, job.reused_from && "file reused",
            job.client && `by ${job.client}`, timeAgo(job.created_at)].filter(Boolean).join(" · ")}
        </p>
        <JobProgress job={job} />
        {job.status === "failed" && job.error && <p className="line-clamp-2 text-xs text-destructive" title={job.error}>{job.error}</p>}
        {job.status === "finished" && job.error && <p className="line-clamp-1 text-xs text-warning" title={job.error}>{job.error}</p>}
      </div>
      <div className="flex shrink-0 flex-col items-end justify-between gap-1.5">
        <Badge variant={status.variant}>{status.label}</Badge>
        <JobActions job={job} compact className="-mr-1.5 -mb-1" />
      </div>
    </li>
  );
}
