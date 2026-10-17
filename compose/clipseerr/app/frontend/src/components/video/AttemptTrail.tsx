import { ChevronRight } from "lucide-react";
import { Fragment } from "react";

import type { Request } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The services of this round, in order: "Aurral ✕ › songdrop ✓". */
export function AttemptTrail({ request }: { request: Request }) {
  const attempts = request.attempts.filter((a) => a.round === request.round);
  if (!attempts.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1 text-xs">
      {attempts.map((attempt, i) => (
        <Fragment key={`${attempt.service_id}-${i}`}>
          {i > 0 && <ChevronRight className="size-3 text-muted-foreground/50" />}
          <span
            title={attempt.message ?? undefined}
            className={cn(
              "rounded-md px-1.5 py-0.5",
              attempt.state === "failed" && "bg-destructive/10 text-destructive line-through decoration-destructive/40",
              attempt.state === "completed" && "bg-success/10 text-success",
              (attempt.state === "sent" || attempt.state === "downloading") && "bg-rose-400/10 text-rose-200",
            )}
          >
            {attempt.service_name}
          </span>
        </Fragment>
      ))}
    </span>
  );
}
