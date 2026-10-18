import { Ban, Download, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorText, fileUrl, type Job } from "@/lib/api";
import { useJobsChanged } from "@/lib/queries";

/** What can be done with a job now: get its file, stop it, try it again, forget it. */
export function JobActions({ job, compact = false }: { job: Job; compact?: boolean }) {
  const changed = useJobsChanged();
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
  const size = compact ? "icon-sm" : "sm";
  return (
    <div className="flex shrink-0 items-center gap-1">
      {video && (
        <Button variant="ghost" size={size} asChild title="Download the file">
          <a href={fileUrl(job, video)} download aria-label="Download the file"><Download />{!compact && "File"}</a>
        </Button>
      )}
      {(job.status === "queued" || job.status === "running") && (
        <Button variant="ghost" size={size} title="Stop it" aria-label="Stop it" onClick={() => void run(() => api.cancelJob(job.id), "Stopped")}>
          <Ban />{!compact && "Stop"}
        </Button>
      )}
      {(job.status === "failed" || job.status === "cancelled") && (
        <Button variant="ghost" size={size} title="Try again" aria-label="Try again" onClick={() => void run(() => api.retryJob(job.id), "Queued again")}>
          <RotateCcw />{!compact && "Try again"}
        </Button>
      )}
      {(job.status === "finished" || job.status === "failed" || job.status === "cancelled") && (
        <Button variant="ghost" size={size} title="Forget it (its files in a destination stay)" aria-label="Forget it"
                onClick={() => void run(() => api.deleteJob(job.id), "Forgotten")}>
          <Trash2 />{!compact && "Forget"}
        </Button>
      )}
    </div>
  );
}
