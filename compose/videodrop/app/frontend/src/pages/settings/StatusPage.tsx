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
  const { jobs, disk } = data;
  return (
    <>
      <PageHeader eyebrow="Settings" title="Status" description="What videodrop runs, and how it is doing."
                  actions={<Button variant="secondary" asChild><a href="/docs"><Braces /> API reference</a></Button>} />
      <Card className="p-5">
        <dl>
          <Row label="Version">videodrop {data.version} · yt-dlp {data.yt_dlp}</Row>
          <Row label="YouTube's challenge">{data.deno ? "answered (Deno)" : <span className="text-warning">no Deno: YouTube may refuse</span>}</Row>
          <Row label="Running since">{timeAgo(data.started_at)}</Row>
          <Row label="Downloading now">{data.running} of {data.max_parallel} at once</Row>
          <Row label="Downloads">{jobs.queued} queued · {jobs.finished} done · {jobs.failed} failed · {jobs.cancelled} cancelled</Row>
          <Row label="Media disk">
            {disk.free_bytes === null ? "unknown" : (
              <span className={disk.low ? "text-destructive" : undefined}>
                {formatBytes(disk.free_bytes)} free{disk.low ? `: under ${formatBytes(disk.min_free_bytes)}, downloads wait` : ""}
              </span>
            )}
          </Row>
          <Row label="Cookies">{data.cookies.present ? (data.cookies.expired ? <span className="text-warning">run out</span> : "saved") : "none"}</Row>
          <Row label="Way out">
            {data.egress.connections} connections to the internet
            {data.egress.refused ? <span className="text-warning"> · {data.egress.refused} refused (addresses on this network)</span> : " · none refused"}
          </Row>
          <Row label="Live streams open">{data.streams}</Row>
          <Row label="Apps that asked">{data.clients.join(", ") || "none yet"}</Row>
          <Row label="Database">{formatBytes(data.database_bytes)}</Row>
        </dl>
      </Card>
    </>
  );
}
