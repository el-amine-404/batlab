import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { Job } from "@/lib/api";
import { useJobs } from "@/lib/jobs";

import { JobRow } from "./JobRow";

const PAGE = 20;

/** Jobs as collapsible rows, 20 at a time. */
export function JobList({ jobs, startOpen }: { jobs: Job[]; startOpen?: boolean }) {
  const { refresh } = useJobs();
  const [shown, setShown] = useState(PAGE);

  return (
    <>
      <Card className="divide-y divide-border overflow-hidden p-0">
        {jobs.slice(0, shown).map((job) => (
          <JobRow key={job.id} job={job} onChange={refresh} startOpen={startOpen} />
        ))}
      </Card>
      {jobs.length > shown && (
        <Button variant="secondary" className="mt-3 self-center" onClick={() => setShown((n) => n + PAGE)}>
          Show {Math.min(PAGE, jobs.length - shown)} more
        </Button>
      )}
    </>
  );
}
