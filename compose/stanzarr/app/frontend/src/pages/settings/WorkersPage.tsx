import { useQueryClient } from "@tanstack/react-query";
import { Cpu, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { CopyButton } from "@/components/common/CopyButton";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorText } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { keys, useWorkers } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

const INSTALL = `python3.12 -m venv ~/.stanzarr-worker
~/.stanzarr-worker/bin/pip install "stanzarr-worker @ git+https://github.com/el-amine-404/batlab.git#subdirectory=compose/stanzarr/app/worker"`;

/** Settings, Workers: the computers lending their GPU to AI sync, and the key they use. */
export default function WorkersPage() {
  const client = useQueryClient();
  const can = useCan();
  const { data, error, refetch, isPending } = useWorkers();

  const newKey = async () => {
    if (!window.confirm("Make a new key? Every worker needs it before its next job.")) return;
    try {
      await api.newWorkerKey();
      toast.success("New key made: give it to the workers.");
      void client.invalidateQueries({ queryKey: keys.workers });
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const run = `~/.stanzarr-worker/bin/stanzarr-worker --server ${window.location.origin} --key ${data.key ?? "<the workers' key>"}`;

  return (
    <>
      <PageHeader eyebrow="Settings" title="Workers"
                  description="AI sync runs on a computer with an NVIDIA GPU (4 GB is enough): a worker takes one job at a time and sends back the synced lines and the voice alone." />
      {!data.workers.length ? (
        <Empty icon={<Cpu />} title="No worker has checked in">Install one on a computer with a GPU (below); it shows here within 20 s.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.workers.map((worker) => (
            <li key={worker.name} className="flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
              <span className={cn("size-2 shrink-0 rounded-full", worker.online ? "bg-success" : "bg-muted-foreground/40")} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{worker.name}</p>
                  {worker.gpu ? <Badge variant="accent">{worker.gpu}{worker.vram ? ` · ${Math.round(worker.vram / 1024)} GB` : ""}</Badge>
                    : <Badge variant="muted">No GPU: takes no job</Badge>}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {worker.online ? "Online" : `Last seen ${timeAgo(worker.last_seen)}`} · stanzarr-worker {worker.version || "?"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Card className="mt-6 flex flex-col gap-4 p-5">
        <h2 className="font-medium">Add a worker</h2>
        <ol className="flex flex-col gap-4 text-sm">
          <li className="flex flex-col gap-1.5">
            <span>1. On the computer with the GPU (Linux, Python 3.12, ffmpeg):</span>
            <pre className="relative overflow-x-auto rounded-lg bg-background/70 p-3 pr-12 font-mono text-xs leading-relaxed">
              {INSTALL}
              <span className="absolute top-1.5 right-1.5"><CopyButton text={INSTALL} label="install commands" /></span>
            </pre>
          </li>
          <li className="flex flex-col gap-1.5">
            <span>2. Run it, with the workers' key:</span>
            <pre className="relative overflow-x-auto rounded-lg bg-background/70 p-3 pr-12 font-mono text-xs">
              {run}
              {data.key && <span className="absolute top-1.5 right-1.5"><CopyButton text={run} label="command" /></span>}
            </pre>
            {!data.key && <p className="text-xs text-muted-foreground">The key is shown to who may change settings.</p>}
          </li>
        </ol>
        {can("settings") && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4 text-sm">
            <span className="text-muted-foreground">The workers' key</span>
            {data.key_fixed ? (
              <span className="text-xs text-muted-foreground">Set by STANZARR_WORKER_KEY: change it there.</span>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => void newKey()}><RefreshCw /> Make a new one</Button>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
