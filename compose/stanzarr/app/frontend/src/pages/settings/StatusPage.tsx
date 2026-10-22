import { Braces } from "lucide-react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
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
  const { data: where } = useQuery({ queryKey: ["library-folders"], queryFn: api.library, staleTime: Infinity });
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const scan = data.last_scan as { at?: number; added?: number; updated?: number; removed?: number; seconds?: number };
  return (
    <>
      <PageHeader eyebrow="Settings" title="Status" description="What stanzarr runs, and how it is doing."
                  actions={<Button variant="secondary" asChild><a href="/docs"><Braces /> API reference</a></Button>} />
      <Card className="p-5">
        <dl>
          <Row label="Version">stanzarr {data.version}</Row>
          <Row label="Running since">{timeAgo(data.started_at)}</Row>
          <Row label="Doing now">{data.background || "nothing: waiting for the next scan"}</Row>
          <Row label="Songs">{data.songs.toLocaleString()}</Row>
          <Row label="Last scan">
            {scan.at ? `${timeAgo(scan.at)}: ${scan.added ?? 0} new, ${scan.updated ?? 0} changed, ${scan.removed ?? 0} gone, ${scan.seconds ?? 0} s` : "not yet"}
          </Row>
          <Row label="AI lines to check">{data.to_check.lines} in {data.to_check.songs} songs</Row>
          <Row label="GPU workers online">{data.workers_online} {data.gpu_online ? "(a GPU is there)" : "(no GPU)"}</Row>
          <Row label="Sources asked">{data.sources.length ? data.sources.join(", ") : "none"}</Row>
          <Row label="Library">{where ? where.folders.join(", ") : "…"}</Row>
          <Row label="Writes into the library">{data.read_only ? "no (read-only)" : "yes"}</Row>
          <Row label="Database">{formatBytes(data.database_bytes)}</Row>
        </dl>
      </Card>
    </>
  );
}
