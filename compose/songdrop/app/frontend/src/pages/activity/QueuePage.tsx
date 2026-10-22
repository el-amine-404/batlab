import { Inbox } from "lucide-react";
import { Link } from "react-router";

import { JobList } from "@/components/jobs/JobList";
import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { useJobs } from "@/lib/jobs";

export function QueuePage() {
  const { jobs, queue, error } = useJobs();

  return (
    <>
      <PageHeader title="Queue" description="Downloads running now, and the ones waiting for their turn." />
      {error && <Failed message={error} />}
      {!jobs && !error && <Loading />}
      {jobs && queue.length === 0 && (
        <Empty icon={Inbox} title="Nothing is downloading">
          <Link to="/" className="text-primary hover:underline">
            Add a link
          </Link>{" "}
          to start. Finished downloads are in History.
        </Empty>
      )}
      {queue.length > 0 && <JobList jobs={queue} startOpen />}
    </>
  );
}
