import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Cpu, HardDrive, Info, KeyRound, Laptop, Network, Pencil, Plus, ShieldCheck, Sparkles, Trash2, TriangleAlert } from "lucide-react";
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { api, errorText, type Worker } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { keys, useWorkers } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

const INSTALL = `python3.12 -m venv ~/.stanzarr-worker
~/.stanzarr-worker/bin/pip install "stanzarr-worker @ git+https://github.com/el-amine-404/batlab.git#subdirectory=compose/stanzarr/app/worker"`;

const runCommand = (key: string) => `~/.stanzarr-worker/bin/stanzarr-worker --server ${window.location.origin} --key ${key}`;

function cleanHost(host?: string | null): string {
  if (!host) return "";
  const trimmed = host.trim();
  const m = trimmed.match(/^([\d.]+)\s*\(([\w.-]+)\)$/);
  if (m) {
    const [, ip, h] = m;
    if (h.replace(/-/g, ".") === ip || h.toLowerCase() === "localhost" || h === ip) {
      return ip;
    }
  }
  return trimmed;
}

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
  const [showGuide, setShowGuide] = useState(false);

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
      <DialogContent className="max-w-md">
        <form onSubmit={(e) => void submit(e)}>
          <DialogHeader>
            <DialogTitle>{worker ? "Rename the worker" : "Add a worker"}</DialogTitle>
            <DialogDescription>{worker ? "Its key stays the same." : "Each worker gets a dedicated key to securely authenticate and process AI sync jobs."}</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <div>
              <Label htmlFor="worker-name">Worker Name</Label>
              <Input id="worker-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={64} placeholder="e.g. desktop-gpu, laptop" />
            </div>

            {!worker && (
              <div className="rounded-lg border border-border/80 bg-muted/40 p-3 text-xs text-muted-foreground">
                <button
                  type="button"
                  onClick={() => setShowGuide(!showGuide)}
                  className="flex w-full items-center justify-between font-medium text-foreground hover:text-primary transition-colors"
                >
                  <span>Installation instructions guide</span>
                  <ChevronRight className={cn("size-3.5 transition-transform", showGuide && "rotate-90")} />
                </button>
                {showGuide && (
                  <div className="mt-3 space-y-2">
                    <p>On the machine with the GPU (Linux, Python 3.12, ffmpeg):</p>
                    <Code text={INSTALL} label="install commands" />
                  </div>
                )}
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy || !name.trim()}>{worker ? "Rename" : "Create Worker Key"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Detailed Worker Inspection Modal */
function WorkerDetailDialog({ worker, open, onClose, onForget, onRename }: {
  worker: Worker | null;
  open: boolean;
  onClose: () => void;
  onForget: (w: Worker) => void;
  onRename: (w: Worker) => void;
}) {
  const can = useCan();
  if (!worker) return null;

  const host = cleanHost(worker.host);
  const vramGb = worker.vram ? `${(worker.vram / 1024).toFixed(1)} GB` : "Unknown";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <span className={cn("size-2.5 rounded-full shrink-0", worker.online ? "bg-success shadow-[0_0_8px_rgba(34,197,94,0.6)]" : "bg-muted-foreground/40")} />
            <DialogTitle className="text-lg font-semibold">{worker.name}</DialogTitle>
            <Badge variant={worker.online ? "accent" : "muted"} className="ml-auto">
              {worker.online ? "Online & Ready" : "Offline"}
            </Badge>
          </div>
          <DialogDescription>
            {worker.online ? "Checking in regularly every 20 seconds." : worker.last_seen ? `Last seen ${timeAgo(worker.last_seen)}.` : "Never connected yet."}
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-4 text-sm">
          {/* Hardware & Network */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-border/70 bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                <HardDrive className="size-3.5 text-primary" />
                <span className="font-medium">GPU Hardware</span>
              </div>
              <p className="font-medium text-foreground">{worker.gpu || "No GPU detected"}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{worker.vram ? `${vramGb} VRAM` : "CPU fallback mode"}</p>
            </div>

            <div className="rounded-xl border border-border/70 bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                <Network className="size-3.5 text-primary" />
                <span className="font-medium">Network & Host</span>
              </div>
              <p className="font-medium font-mono text-foreground text-xs">{host || "Local loopback"}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{worker.version ? `stanzarr-worker ${worker.version}` : "Protocol v1"}</p>
            </div>
          </div>

          {/* AI Models & Architecture */}
          <div className="rounded-xl border border-border/70 bg-card p-3.5 space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Sparkles className="size-3.5 text-primary" />
              <span>AI Models & Synchronization Pipeline</span>
            </div>
            <div className="space-y-1.5 text-xs text-foreground/90">
              <div className="flex items-start gap-2">
                <span className="font-semibold text-primary/90 shrink-0">• Demucs:</span>
                <span className="text-muted-foreground">HTDemucs neural network isolates vocal audio stems from instruments.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="font-semibold text-primary/90 shrink-0">• Whisper:</span>
                <span className="text-muted-foreground">faster-whisper large-v3 & large-v3-turbo align line and word timings with automatic CPU fallback.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="font-semibold text-primary/90 shrink-0">• Meta MMS:</span>
                <span className="text-muted-foreground">Massively Multilingual Speech aligner cross-validates timestamps and Darija/Arabizi phonemes.</span>
              </div>
            </div>
          </div>

          {/* Capabilities & Security */}
          <div className="rounded-xl border border-border/70 bg-muted/30 p-3 text-xs space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground flex items-center gap-1.5">
                <ShieldCheck className="size-3.5 text-success" /> Key Authentication:
              </span>
              <span className="font-medium">
                {worker.shared_key ? <Badge variant="warning">Legacy Shared Key</Badge> : <code>{worker.prefix}…</code>}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Capabilities:</span>
              <span className="font-medium text-foreground">Forced Alignment (`ai_sync`), Audio Transcription (`ai_generate`)</span>
            </div>
            <div className="flex items-center justify-between pt-1 border-t border-border/50 text-muted-foreground/80">
              <span>Retention Policy:</span>
              <span>Workers inactive for &gt; 15 days are automatically pruned</span>
            </div>
          </div>
        </DialogBody>

        <DialogFooter className="flex items-center justify-between sm:justify-between w-full">
          {can("settings") && (
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => {
                onClose();
                onForget(worker);
              }}
              className="gap-1.5"
            >
              <Trash2 className="size-3.5" /> Forget worker
            </Button>
          )}
          <div className="flex items-center gap-2">
            {can("settings") && worker.id && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  onClose();
                  onRename(worker);
                }}
              >
                <Pencil className="size-3.5 mr-1" /> Rename
              </Button>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Settings, Workers: the computers lending their GPU to AI sync.
 */
export default function WorkersPage() {
  const client = useQueryClient();
  const can = useCan();
  const { data, error, refetch, isPending } = useWorkers();
  const [editing, setEditing] = useState<Worker | "new" | null>(null);
  const [selectedWorker, setSelectedWorker] = useState<Worker | null>(null);
  const [made, setMade] = useState<{ key: string; name: string } | null>(null);

  const forget = async (worker: Worker) => {
    if (!window.confirm(`Forget worker "${worker.name}"? It will be removed from the registry; any running job will be returned to the queue.`)) return;
    try {
      if (worker.id) {
        await api.removeWorker(worker.id);
      } else {
        await api.forgetWorker(worker.name);
      }
      toast.success(`Worker "${worker.name}" was forgotten.`);
      void client.invalidateQueries({ queryKey: keys.workers });
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  const retire = async () => {
    if (!window.confirm("Retire the legacy shared key? Any worker still using it will stop receiving jobs until given a dedicated key.")) return;
    try {
      await api.retireSharedKey();
      toast.success("The shared key was retired.");
      void client.invalidateQueries({ queryKey: keys.workers });
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const onShared = data.workers.filter((w) => w.shared_key);

  return (
    <TooltipProvider>
      <PageHeader
        eyebrow="Settings"
        title="Workers"
        description="AI sync and speech-to-text generation run on networked computers with NVIDIA GPUs or CPU fallbacks. Click any worker to view model architecture and hardware details."
        actions={can("settings") && <Button onClick={() => setEditing("new")}><Plus /> Add a worker</Button>}
      />

      {made && (
        <Card className="mb-6 flex flex-col gap-3 border-primary/40 p-5 bg-card shadow-sm">
          <p className="flex items-center gap-2 font-medium text-foreground">
            <KeyRound className="size-4 text-primary" /> {made.name}'s dedicated key: copy it now, it won't be shown again
          </p>
          <Code text={made.key} label="key" />
          <p className="text-sm text-muted-foreground">On {made.name}, install the worker and run:</p>
          <Code text={runCommand(made.key)} label="command" />
          <div><Button variant="secondary" size="sm" onClick={() => setMade(null)}>Done</Button></div>
        </Card>
      )}

      {!data.workers.length ? (
        <Empty icon={<Cpu />} title="No workers registered yet">
          Click "Add a worker" to generate a key, then start stanzarr-worker on your GPU or CPU machine.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {data.workers.map((worker) => {
            const host = cleanHost(worker.host);
            return (
              <li
                key={worker.id ?? `shared-${worker.name}`}
                onClick={() => setSelectedWorker(worker)}
                className="group flex cursor-pointer items-center gap-3.5 rounded-xl border border-border bg-card p-3.5 sm:p-4 hover:border-primary/50 hover:bg-accent/30 transition-all shadow-xs"
              >
                <span
                  className={cn(
                    "size-2.5 shrink-0 rounded-full transition-transform group-hover:scale-110",
                    worker.online ? "bg-success shadow-[0_0_8px_rgba(34,197,94,0.6)]" : "bg-muted-foreground/40"
                  )}
                  aria-label={worker.online ? "Online" : "Offline"}
                />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium text-foreground text-sm group-hover:text-primary transition-colors">
                      {worker.name}
                    </p>
                    {worker.gpu ? (
                      <Badge variant="accent">
                        {worker.gpu}{worker.vram ? ` · ${Math.round(worker.vram / 1024)} GB` : ""}
                      </Badge>
                    ) : worker.last_seen ? (
                      <Badge variant="muted">CPU Mode</Badge>
                    ) : null}
                    {worker.shared_key && <Badge variant="warning">Shared key</Badge>}
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                    <span className={worker.online ? "text-success font-medium" : ""}>
                      {worker.online ? "Online" : worker.last_seen ? `Last seen ${timeAgo(worker.last_seen)}` : "Never checked in"}
                    </span>
                    {host && (
                      <>
                        <span>·</span>
                        <code className="rounded bg-muted px-1.5 py-0.2 font-mono text-[11px] text-foreground/90">{host}</code>
                      </>
                    )}
                    {worker.model && (
                      <>
                        <span>·</span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="font-medium text-foreground hover:underline cursor-help">
                              {worker.model}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="max-w-xs text-xs">
                            <p className="font-semibold mb-1">Architecture & Pipeline:</p>
                            <p>• Demucs htdemucs: vocal stem separation</p>
                            <p>• faster-whisper large-v3: precision timing</p>
                            <p>• Meta MMS: multi-language phonetic cross-validation</p>
                          </TooltipContent>
                        </Tooltip>
                      </>
                    )}
                    {worker.version && <span>· v{worker.version}</span>}
                  </div>
                </div>

                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`View details of ${worker.name}`}
                    onClick={() => setSelectedWorker(worker)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Info className="size-4" />
                  </Button>
                  {can("settings") && worker.id && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Rename ${worker.name}`}
                      onClick={() => setEditing(worker)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <Pencil className="size-4" />
                    </Button>
                  )}
                  {can("settings") && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Forget ${worker.name}`}
                      onClick={() => void forget(worker)}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* Collapsible Worker Setup Guide (clean & on-demand, not dominating page) */}
      <details className="mt-8 rounded-xl border border-border bg-card/60 p-4 text-sm text-muted-foreground transition-all">
        <summary className="cursor-pointer font-medium text-foreground hover:text-primary transition-colors flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Laptop className="size-4 text-primary" /> Setup & Running a Worker on another computer
          </span>
          <span className="text-xs text-muted-foreground font-normal">Click to expand</span>
        </summary>
        <div className="mt-4 space-y-3 pt-3 border-t border-border/60">
          <p>On your GPU machine (Linux, Python 3.12, NVIDIA driver, ffmpeg):</p>
          <Code text={INSTALL} label="install commands" />
          <p>Then launch the worker with the key generated in "Add a worker":</p>
          <Code text={runCommand("<worker-key>")} label="run command" />
          <p className="text-xs text-muted-foreground">
            Workers report their GPU, VRAM, and IP address every 20 seconds. If a worker is inactive for more than 15 days, it is automatically pruned from the active list.
          </p>
        </div>
      </details>

      {/* Shared Key Notice (Compact admin-only note if legacy keys still exist) */}
      {data.shared_key && can("settings") && (
        <div className="mt-4 rounded-xl border border-warning/30 bg-warning/5 p-4 text-xs text-muted-foreground flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-warning shrink-0" />
            <span>
              <strong>Legacy shared key active:</strong> {onShared.length ? `${onShared.map((w) => w.name).join(", ")} use it.` : "No worker is currently using it."} Consider retiring it once all workers have unique keys.
            </span>
          </div>
          {!data.shared_key_fixed && (
            <Button variant="secondary" size="sm" onClick={() => void retire()} className="shrink-0 h-7 text-xs">
              Retire shared key
            </Button>
          )}
        </div>
      )}

      {editing && (
        <NameDialog
          key={editing === "new" ? "new" : editing.id ?? ""}
          open
          worker={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onMade={(key, name) => setMade({ key, name })}
        />
      )}

      {selectedWorker && (
        <WorkerDetailDialog
          worker={selectedWorker}
          open={Boolean(selectedWorker)}
          onClose={() => setSelectedWorker(null)}
          onForget={(w) => void forget(w)}
          onRename={(w) => setEditing(w)}
        />
      )}
    </TooltipProvider>
  );
}
