import { useQueryClient } from "@tanstack/react-query";
import { Cpu, KeyRound, Pencil, Plus, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/common/CopyButton";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type Worker } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { keys, useWorkers } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

const INSTALL = `python3.12 -m venv ~/.stanzarr-worker
~/.stanzarr-worker/bin/pip install "stanzarr-worker @ git+https://github.com/el-amine-404/batlab.git#subdirectory=compose/stanzarr/app/worker"`;

const runCommand = (key: string) => `~/.stanzarr-worker/bin/stanzarr-worker --server ${window.location.origin} --key ${key}`;

function Code({ text, label }: { text: string; label: string }) {
  return (
    <pre className="relative overflow-x-auto rounded-lg bg-background/70 p-3 pr-12 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
      {text}
      <span className="absolute top-1.5 right-1.5"><CopyButton text={text} label={label} /></span>
    </pre>
  );
}

/** Name a worker (a new one, or one renamed). */
function NameDialog({ worker, open, onClose, onMade }: {
  worker?: Worker;
  open: boolean;
  onClose: () => void;
  onMade: (key: string, name: string) => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState(worker?.name ?? "");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (worker?.id) {
        await api.renameWorker(worker.id, name.trim());
        toast.success("Renamed.");
      } else {
        const made = await api.addWorker(name.trim());
        onMade(made.key, made.worker.name);
      }
      void client.invalidateQueries({ queryKey: keys.workers });
      onClose();
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <form onSubmit={(e) => void submit(e)}>
          <DialogHeader>
            <DialogTitle>{worker ? "Rename the worker" : "Add a worker"}</DialogTitle>
            <DialogDescription>{worker ? "Its key stays the same." : "It gets a key of its own: shown once, then only its first letters."}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Label htmlFor="worker-name">Name</Label>
            <Input id="worker-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="laptop" />
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy || !name.trim()}>{worker ? "Rename" : "Add"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Settings, Workers: the computers lending their GPU to AI sync. Each has a
 * key of its own: added here (the key shown once), renamed, removed (its key
 * stops working at once). The key all workers shared before 1.1 works until retired.
 */
export default function WorkersPage() {
  const client = useQueryClient();
  const can = useCan();
  const { data, error, refetch, isPending } = useWorkers();
  const [editing, setEditing] = useState<Worker | "new" | null>(null);
  const [made, setMade] = useState<{ key: string; name: string } | null>(null);

  const remove = async (worker: Worker) => {
    if (!worker.id || !window.confirm(`Remove ${worker.name}? Its key stops working at once; a job it runs goes back to the queue.`)) return;
    try {
      await api.removeWorker(worker.id);
      toast.success(`${worker.name} removed.`);
      void client.invalidateQueries({ queryKey: keys.workers });
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };
  const retire = async () => {
    if (!window.confirm("Retire the shared key? Workers still using it stop getting jobs until they have a key of their own.")) return;
    try {
      await api.retireSharedKey();
      toast.success("The shared key no longer works.");
      void client.invalidateQueries({ queryKey: keys.workers });
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const onShared = data.workers.filter((w) => w.shared_key);

  return (
    <>
      <PageHeader eyebrow="Settings" title="Workers"
                  description="AI sync runs on a computer with an NVIDIA GPU (4 GB is enough): a worker takes one job at a time and sends back the synced lines and the voice alone."
                  actions={can("settings") && <Button onClick={() => setEditing("new")}><Plus /> Add a worker</Button>} />

      {made && (
        <Card className="mb-6 flex flex-col gap-3 border-primary/40 p-5">
          <p className="flex items-center gap-2 font-medium"><KeyRound className="size-4 text-primary" /> {made.name}'s key: copy it now, it isn't shown again</p>
          <Code text={made.key} label="key" />
          <p className="text-sm">On {made.name}, install the worker (below) and run:</p>
          <Code text={runCommand(made.key)} label="command" />
          <div><Button variant="secondary" size="sm" onClick={() => setMade(null)}>Done</Button></div>
        </Card>
      )}

      {!data.workers.length ? (
        <Empty icon={<Cpu />} title="No worker yet">Add one, then run it on a computer with a GPU: it shows here within 20 s.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.workers.map((worker) => (
            <li key={worker.id ?? `shared-${worker.name}`} className="flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
              <span className={cn("size-2 shrink-0 rounded-full", worker.online ? "bg-success" : "bg-muted-foreground/40")}
                    aria-label={worker.online ? "Online" : "Offline"} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{worker.name}</p>
                  {worker.gpu ? <Badge variant="accent">{worker.gpu}{worker.vram ? ` · ${Math.round(worker.vram / 1024)} GB` : ""}</Badge>
                    : worker.last_seen ? <Badge variant="muted">No GPU: takes no job</Badge> : null}
                  {worker.shared_key && <Badge variant="warning">Shared key</Badge>}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {worker.online ? "Online" : worker.last_seen ? `Last seen ${timeAgo(worker.last_seen)}` : "Never checked in"}
                  {worker.version && ` · stanzarr-worker ${worker.version}`}
                  {worker.prefix && <> · key <code>{worker.prefix}…</code></>}
                  {worker.created_at && ` · added ${timeAgo(worker.created_at)}`}
                </p>
              </div>
              {can("settings") && worker.id && (
                <div className="flex gap-1">
                  <Button variant="ghost" size="icon-sm" aria-label={`Rename ${worker.name}`} onClick={() => setEditing(worker)}><Pencil /></Button>
                  <Button variant="ghost" size="icon-sm" aria-label={`Remove ${worker.name}`} onClick={() => void remove(worker)}><Trash2 /></Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {data.shared_key && can("settings") && (
        <Card className="mt-6 flex flex-col gap-2 border-warning/30 p-5 text-sm">
          <p className="flex items-center gap-2 font-medium"><TriangleAlert className="size-4 text-warning" /> The shared key from before 1.1 still works</p>
          <p className="text-muted-foreground">
            {onShared.length ? `${onShared.map((w) => w.name).join(", ")} use${onShared.length === 1 ? "s" : ""} it. ` : "No worker used it lately. "}
            Give each worker a key of its own (Add a worker), then retire it: a leaked key then only lets in one worker, and removing it shuts that one out.
          </p>
          <div>
            {data.shared_key_fixed
              ? <p className="text-xs text-muted-foreground">It is set by STANZARR_WORKER_KEY: remove it from compose/.env, then restart stanzarr.</p>
              : <Button variant="secondary" size="sm" onClick={() => void retire()}>Retire the shared key</Button>}
          </div>
        </Card>
      )}

      <Card className="mt-6 flex flex-col gap-3 p-5 text-sm">
        <h2 className="font-medium">Installing a worker</h2>
        <p>On the computer with the GPU (Linux, Python 3.12, ffmpeg):</p>
        <Code text={INSTALL} label="install commands" />
        <p>Then run it with its key (Add a worker gives the whole command):</p>
        <Code text={runCommand("<its key>")} label="command" />
      </Card>

      {editing && (
        <NameDialog key={editing === "new" ? "new" : editing.id ?? ""} open worker={editing === "new" ? undefined : editing}
                    onClose={() => setEditing(null)} onMade={(key, name) => setMade({ key, name })} />
      )}
    </>
  );
}
