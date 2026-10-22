import { PartyPopper } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { MissingSongRow } from "@/components/jobs/JobRow";
import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { Card } from "@/components/ui/card";
import { ACTIVE, api, errorText } from "@/lib/api";
import { useJobs } from "@/lib/jobs";

export function MissingPage() {
  const { jobs, missing, error, refresh } = useJobs();
  const [acting, setActing] = useState(false);

  const retry = async (jobId: string, index: number) => {
    setActing(true);
    try {
      await api.retrySong(jobId, index);
      refresh();
    } catch (caught) {
      toast.error("Couldn't try again", { description: errorText(caught) });
    } finally {
      setActing(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Missing"
        description={
          <>
            Songs a download couldn't get. songdrop tries each again on its own (
            <Link to="/settings/retries" className="text-primary hover:underline">
              Settings, Retries
            </Link>
            ); Retry now tries at once.
          </>
        }
      />
      {error && <Failed message={error} />}
      {!jobs && !error && <Loading />}
      {jobs && missing.length === 0 && <Empty icon={PartyPopper} title="No missing songs" />}
      {missing.length > 0 && (
        <Card className="px-4 py-2">
          <ul className="divide-y divide-border [&>li]:py-3">
            {missing.map(({ job, song }, position) => (
              <MissingSongRow
                key={`${job.id}-${song.index ?? position}`}
                song={song}
                from={job.title ?? job.url}
                canRetry={!acting && !ACTIVE.includes(job.status)}
                onRetry={() => song.index !== null && retry(job.id, song.index)}
              />
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
