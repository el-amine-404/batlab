import { RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Failed, Loading } from "@/components/layout/States";
import { SettingGroup } from "@/components/settings/rows";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { formatBytes } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  queued: "Waiting",
  running: "Downloading",
  done: "Complete",
  partial: "Songs missing",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function StatusPage() {
  const { data: status, error, reload } = useLoad(api.status);

  return (
    <>
      <PageHeader
        title="Status"
        description="The tools songdrop runs, the space left where it writes, and its downloads at a glance."
        actions={
          <Button variant="secondary" size="sm" onClick={() => void reload()}>
            <RefreshCw /> Refresh
          </Button>
        }
      />
      {error && <Failed message={error} />}
      {!status && !error && <Loading />}
      {status && (
        <div className="flex flex-col gap-6">
          <SettingGroup title="Tools" description={`songdrop started ${new Date(status.started_at * 1000).toLocaleString()}. The versions change with its image (compose/versions.env).`}>
            {status.tools.map((tool) => (
              <div key={tool.name} className="grid gap-1 px-4 py-3 sm:grid-cols-[10rem_9rem_minmax(0,1fr)] sm:items-center sm:gap-4">
                <span className="text-sm font-medium">{tool.name}</span>
                <code className="font-mono text-sm">{tool.version ?? <span className="text-destructive">not found</span>}</code>
                <span className="text-xs text-muted-foreground">{tool.role}</span>
              </div>
            ))}
          </SettingGroup>

          <SettingGroup title="Disk space">
            {status.disks.map((disk) => {
              const used = disk.total_bytes && disk.free_bytes !== null ? 100 - (disk.free_bytes / disk.total_bytes) * 100 : null;
              return (
                <div key={disk.path} className="flex flex-col gap-2 px-4 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                    <span>
                      <span className="font-medium">{disk.label}</span> <code className="ml-1 font-mono text-xs text-muted-foreground">{disk.path}</code>
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {disk.free_bytes !== null && disk.total_bytes ? `${formatBytes(disk.free_bytes)} free of ${formatBytes(disk.total_bytes)}` : "Not readable"}
                    </span>
                  </div>
                  {used !== null && <Progress value={used} className="h-1.5" />}
                </div>
              );
            })}
          </SettingGroup>

          <SettingGroup title="Downloads">
            <div className="flex flex-wrap gap-x-8 gap-y-3 px-4 py-4">
              {Object.keys(STATUS_LABELS).map((key) => (
                <div key={key}>
                  <p className="text-2xl font-semibold tabular-nums">{status.jobs.by_status[key] ?? 0}</p>
                  <p className="text-xs text-muted-foreground">{STATUS_LABELS[key]}</p>
                </div>
              ))}
              <div>
                <p className="text-2xl font-semibold tabular-nums">{status.jobs.songs_waiting}</p>
                <p className="text-xs text-muted-foreground">Songs waiting for a try</p>
              </div>
            </div>
          </SettingGroup>
        </div>
      )}
    </>
  );
}
