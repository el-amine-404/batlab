import { Download, Search } from "lucide-react";
import { useDeferredValue, useState } from "react";
import { useSearchParams } from "react-router";

import { NewDownload } from "@/components/jobs/NewDownload";
import { JobRow } from "@/components/jobs/JobRow";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { JobCounts, Status } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { useCounts, useJobs } from "@/lib/queries";
import { cn } from "@/lib/utils";

const TABS: { key: string; label: string; status: Status[] }[] = [
  { key: "all", label: "All", status: [] },
  { key: "active", label: "In progress", status: ["running", "queued"] },
  { key: "finished", label: "Done", status: ["finished"] },
  { key: "failed", label: "Failed", status: ["failed", "cancelled"] },
];

function count(counts: JobCounts | undefined, status: Status[]): number | undefined {
  if (!counts) return undefined;
  const wanted = status.length ? status : (Object.keys(counts) as Status[]);
  return wanted.reduce((sum, key) => sum + counts[key], 0);
}

export function DownloadsPage() {
  const can = useCan();
  // The tab is in the address: a link (Issues) can open Failed.
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get("tab")) ?? TABS[0];
  const setTab = (next: (typeof TABS)[number]) => setParams(next.key === "all" ? {} : { tab: next.key }, { replace: true });
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const { data: counts } = useCounts();
  const { data, error, isPending, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useJobs({ status: tab.status, q: q || undefined });
  const jobs = data?.pages.flatMap((page) => page.jobs) ?? [];

  return (
    <>
      <PageHeader title="Downloads"
                  description={can("everyone") ? "Videos asked for here or by other apps, as they go." : "The videos you asked for, as they go."} />
      {can("download") && <NewDownload />}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-1 overflow-x-auto rounded-lg border border-border bg-card p-1" role="tablist">
          {TABS.map((item) => (
            <button key={item.key} type="button" role="tab" aria-selected={item.key === tab.key} onClick={() => setTab(item)}
                    className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground",
                                  item.key === tab.key && "bg-accent font-medium text-foreground")}>
              {item.label}
              <span className="text-xs tabular-nums text-muted-foreground">{count(counts, item.status) ?? ""}</span>
            </button>
          ))}
        </div>
        <div className="relative sm:w-64">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search titles and links" className="pl-9" type="search" />
        </div>
      </div>

      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} inline />
      ) : !jobs.length ? (
        <Empty icon={<Download />} title={q || tab.status.length ? "Nothing here" : "No download yet"}>
          {q || tab.status.length ? "Nothing matches." : "Paste a link above, or let an app ask through the API."}
        </Empty>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {jobs.map((job) => <JobRow key={job.id} job={job} />)}
          </ul>
          {hasNextPage && (
            <div className="mt-4 flex justify-center">
              <Button variant="secondary" onClick={() => void fetchNextPage()} disabled={isFetchingNextPage}>
                {isFetchingNextPage ? "Loading…" : "Show more"}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}
