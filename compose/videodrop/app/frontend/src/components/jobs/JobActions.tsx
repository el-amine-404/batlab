import { Ban, Download, Play, RotateCcw, Trash2 } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorText, fileUrl, type Job } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { useJobsChanged } from "@/lib/queries";
import { cn } from "@/lib/utils";

/**
 * What can be done with a job now, in the order it is most wanted: play it,
 * get its file, stop it, try it again (or download it again once its files
 * are gone), forget it. compact: icons only (the
 * list); else labelled, play first and plain to see (a job's page).
 */
export function JobActions({ job, compact = false, className }: { job: Job; compact?: boolean; className?: string }) {
  const changed = useJobsChanged();
  // Stopping, retrying and forgetting are for who may ask for downloads (the server checks whose it is).
  const act = useCan()("download");
  const run = async (work: () => Promise<unknown>, done: string) => {
    try {
      await work();
      toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    changed();
  };
  const video = job.files.find((file) => file.kind === "video" && file.available);
  // Its files removed after their hold (or moved away): the same job downloads them again.
  const gone = job.status === "finished" && job.files.length > 0 && job.files.every((file) => !file.available);
  const size = compact ? "icon-sm" : "sm";
  return (
    <div className={cn("flex shrink-0 flex-wrap items-center gap-1", !compact && "gap-2", className)}>
      {video && (
        <Button variant={compact ? "ghost" : "default"} size={size} asChild title="Play it">
          <Link to={`/jobs/${job.id}/play`} aria-label="Play it"><Play className={compact ? undefined : "fill-current"} />{!compact && "Play"}</Link>
        </Button>
      )}
      {video && (
        <Button variant={compact ? "ghost" : "secondary"} size={size} asChild title="Download the file">
          <a href={fileUrl(job, video)} download aria-label="Download the file"><Download />{!compact && "Download"}</a>
        </Button>
      )}
      {act && (job.status === "queued" || job.status === "running") && (
        <Button variant={compact ? "ghost" : "secondary"} size={size} title="Stop it" aria-label="Stop it" onClick={() => void run(() => api.cancelJob(job.id), "Stopped")}>
          <Ban />{!compact && "Stop"}
        </Button>
      )}
      {act && (job.status === "failed" || job.status === "cancelled") && (
        <Button variant={compact ? "ghost" : "default"} size={size} title="Try again" aria-label="Try again" onClick={() => void run(() => api.retryJob(job.id), "Queued again")}>
          <RotateCcw />{!compact && "Try again"}
        </Button>
      )}
      {act && gone && (
        <Button variant={compact ? "ghost" : "default"} size={size} title="Its files are gone: download it again" aria-label="Download it again"
                onClick={() => void run(() => api.retryJob(job.id), "Queued again")}>
          <RotateCcw />{!compact && "Download again"}
        </Button>
      )}
      {act && (job.status === "finished" || job.status === "failed" || job.status === "cancelled") && (
        <Button variant="ghost" size={size} title="Forget it (its files in a destination stay)" aria-label="Forget it"
                className="hover:text-destructive" onClick={() => void run(() => api.deleteJob(job.id), "Forgotten")}>
          <Trash2 />{!compact && "Forget"}
        </Button>
      )}
    </div>
  );
}
