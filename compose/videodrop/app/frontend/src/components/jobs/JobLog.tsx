import { LogView } from "@/components/common/LogView";
import { Card } from "@/components/ui/card";
import { api, type Job } from "@/lib/api";
import { useLogLines } from "@/lib/useLogLines";

/** What happened to a download, line by line; followed live while it is queued or running. */
export function JobLog({ job }: { job: Job }) {
  const active = job.status === "queued" || job.status === "running";
  const { lines, loading, error } = useLogLines(job.id, (after, signal) => api.jobLog(job.id, after, signal), active);
  return (
    <Card className="mt-4 gap-0 p-4">
      <div className="mb-3">
        <p className="font-medium">Log</p>
        <p className="text-xs text-muted-foreground">
          What happened to it{active ? ", as it happens" : ""}. Passwords, tokens and signed addresses are hidden; the newest 3000 lines are kept, until it's forgotten.
        </p>
      </div>
      <LogView lines={lines} loading={loading} error={error} file={{ url: `/api/v1/jobs/${job.id}/log.txt`, name: `videodrop-${job.id.slice(0, 8)}.log` }} />
    </Card>
  );
}
