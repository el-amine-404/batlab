import { Clock } from "lucide-react";

import { Tooltip } from "@/components/ui/tooltip";
import type { Job } from "@/lib/api";
import { useNow } from "@/lib/useNow";
import { cn, duration, formatWhen } from "@/lib/utils";

/**
 * How long a held file stays: "removed in 21 h 5 min", in amber in its last
 * hour; on hover, when exactly, and what happens after.
 */
export function Expiry({ job, at, className }: { job: Job; at: number; className?: string }) {
  const now = useNow();
  const left = at - now;
  return (
    <Tooltip content={
      <div className="flex flex-col gap-1">
        <span className="font-medium">Removed {formatWhen(at)}</span>
        <span className="text-muted-foreground">
          A download without a destination is kept {job.hold_hours} h for whoever asked for it. After that its link answers
          “410 Gone”, and “Download again” gets it back.
        </span>
      </div>
    }>
      <span tabIndex={0} className={cn("inline-flex cursor-help items-center gap-1", left < 3600 && "text-warning", className)}>
        <Clock className="size-3.5 shrink-0" />
        {left > 0 ? `removed in ${duration(left)}` : "being removed"}
      </span>
    </Tooltip>
  );
}
