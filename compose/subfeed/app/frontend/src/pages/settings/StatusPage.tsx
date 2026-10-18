import { Braces } from "lucide-react";
import type { ReactNode } from "react";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useStatus } from "@/lib/queries";
import { formatBytes, timeAgo } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-border py-2.5 text-sm last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium tabular-nums">{children}</dd>
    </div>
  );
}

export default function StatusPage() {
  const { data, error, isPending, refetch } = useStatus();
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader eyebrow="Settings" title="Status" description="What subfeed runs, and how it is doing."
                  actions={<Button variant="secondary" asChild><a href="/docs"><Braces /> API reference</a></Button>} />
      <Card className="p-5">
        <dl>
          <Row label="Version">subfeed {data.version}</Row>
          <Row label="Running since">{timeAgo(data.started_at)}</Row>
          <Row label="Feeds">{data.feeds.total} ({data.feeds.enabled} read{data.feeds.failing ? `, ${data.feeds.failing} failing` : ""})</Row>
          <Row label="Posts kept">{data.posts.toLocaleString()}</Row>
          <Row label="Database">{formatBytes(data.database_bytes)}</Row>
          <Row label="Last round of reads">{data.last_round ? timeAgo(data.last_round) : "not yet"}</Row>
          <Row label="Sources">{data.sources.join(", ")}</Row>
        </dl>
      </Card>
    </>
  );
}
