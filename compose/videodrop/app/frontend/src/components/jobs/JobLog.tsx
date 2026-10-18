import { ChevronDown, ScrollText } from "lucide-react";
import { useState } from "react";

import { LogView } from "@/components/common/LogView";
import { Card } from "@/components/ui/card";
import { api, type Job } from "@/lib/api";
import { useLogLines } from "@/lib/useLogLines";
import { cn } from "@/lib/utils";

function Lines({ job }: { job: Job }) {
  const active = job.status === "queued" || job.status === "running";
  const { lines, loading, error } = useLogLines(job.id, (after, signal) => api.jobLog(job.id, after, signal), active);
  return (
    <LogView lines={lines} loading={loading} error={error} className="mt-1"
             file={{ url: `/api/v1/jobs/${job.id}/log.txt`, name: `videodrop-${job.id.slice(0, 8)}.log` }} />
  );
}

/**
 * What happened to a download, line by line: closed to start with (read
 * only once opened); followed live while it is queued or running.
 */
export function JobLog({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="mt-4 gap-0 p-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full items-center gap-3 rounded-xl p-4 text-left hover:bg-white/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ScrollText className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">Log</p>
          <p className="text-xs text-muted-foreground">What happened to it, line by line: for when something went wrong. Passwords, tokens and signed addresses are hidden.</p>
        </div>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="px-4 pb-4"><Lines job={job} /></div>}
    </Card>
  );
}
