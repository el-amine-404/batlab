import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileArchive, FileText, Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type ExportRequest, type Project } from "@/lib/api";
import { cn, formatBytes, plural } from "@/lib/utils";

type Kind = "pdf" | "zip";
type Quality = ExportRequest["quality"];

const QUALITIES: Record<Kind, { id: Quality; label: string; hint: string }[]> = {
  pdf: [
    { id: "balanced", label: "Balanced", hint: "Sharp, and a size that can be emailed" },
    { id: "small", label: "Small", hint: "The smallest, still readable" },
    { id: "lossless", label: "Lossless", hint: "Every pixel, the largest" },
  ],
  zip: [
    { id: "balanced", label: "JPEG", hint: "Smaller images" },
    { id: "lossless", label: "PNG", hint: "Lossless images" },
  ],
};

function Choice({ selected, onClick, title, hint }: { selected: boolean; onClick: () => void; title: string; hint?: string }) {
  return (
    <button type="button" role="radio" aria-checked={selected} onClick={onClick}
            className={cn("flex min-h-11 flex-1 flex-col justify-center rounded-lg border px-3 py-2 text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring",
                          selected ? "border-primary bg-primary/10 text-foreground" : "border-border text-foreground hover:bg-accent")}>
      <span className="text-sm font-medium">{title}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </button>
  );
}

/** A PDF or a ZIP of all the pages, a range, or those selected; made, then downloaded by itself. */
export function ExportDialog({ project, initial, selected, onClose }: { project: Project; initial: Kind; selected: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const ready = project.completed_count;
  const [kind, setKind] = useState<Kind>(initial);
  const [quality, setQuality] = useState<Quality>("balanced");
  const [which, setWhich] = useState<"all" | "range" | "selected">(selected.length ? "selected" : "all");
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(ready);
  const [job, setJob] = useState<{ id: string; url: string } | null>(null);
  const started = useRef(false);

  const current = job && project.exports.find((item) => item.id === job.id);
  const status = current?.status ?? (job ? "queued" : null);

  const make = useMutation({
    mutationFn: () => api.createExport(project.public_id, {
      kind, quality,
      ...(which === "range" ? { page_from: from, page_to: to } : which === "selected" ? { page_ids: selected } : {}),
    }),
    onSuccess: (answer) => {
      setJob({ id: answer.id, url: answer.download_url });
      started.current = false;
      void client.invalidateQueries({ queryKey: ["project", project.public_id] });
    },
  });

  // While it is being made, the stream says when it is ready; this is in case the stream can't.
  useEffect(() => {
    if (!job || status === "ready" || status === "failed") return;
    const timer = window.setInterval(() => void client.invalidateQueries({ queryKey: ["project", project.public_id] }), 2000);
    return () => window.clearInterval(timer);
  }, [job, status, client, project.public_id]);

  useEffect(() => {
    if (status !== "ready" || !job || started.current) return;
    started.current = true;
    const link = document.createElement("a");
    link.href = job.url;
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
  }, [status, job]);

  const count = which === "all" ? ready : which === "selected" ? selected.length : Math.max(0, to - from + 1);
  const rangeBad = which === "range" && (from < 1 || to > ready || from > to);
  const Icon = kind === "pdf" ? FileText : FileArchive;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Download {project.title}</DialogTitle>
          <DialogDescription className="sr-only">Choose what to download.</DialogDescription>
        </DialogHeader>
        {!job ? (
          <>
            <DialogBody className="flex flex-col gap-5">
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-2 text-sm font-medium">As</legend>
                <div role="radiogroup" className="flex gap-2">
                  <Choice selected={kind === "pdf"} onClick={() => setKind("pdf")} title="PDF" hint="One document" />
                  <Choice selected={kind === "zip"} onClick={() => { setKind("zip"); if (quality === "small") setQuality("balanced"); }} title="ZIP" hint="One image per page" />
                </div>
              </fieldset>
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-2 text-sm font-medium">Pages</legend>
                <div role="radiogroup" className="flex flex-wrap gap-2">
                  <Choice selected={which === "all"} onClick={() => setWhich("all")} title={`All ${ready}`} />
                  <Choice selected={which === "range"} onClick={() => setWhich("range")} title="A range" />
                  {selected.length > 0 && <Choice selected={which === "selected"} onClick={() => setWhich("selected")} title={`${selected.length} selected`} />}
                </div>
                {which === "range" && (
                  <div className="mt-1 flex items-end gap-2">
                    <div className="flex flex-col gap-1"><Label htmlFor="from">From</Label><Input id="from" className="w-24" type="number" inputMode="numeric" min={1} max={ready} value={from} onChange={(event) => setFrom(Number(event.target.value))} /></div>
                    <div className="flex flex-col gap-1"><Label htmlFor="to">To</Label><Input id="to" className="w-24" type="number" inputMode="numeric" min={from} max={ready} value={to} onChange={(event) => setTo(Number(event.target.value))} /></div>
                  </div>
                )}
              </fieldset>
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-2 text-sm font-medium">Quality</legend>
                <div role="radiogroup" className="flex flex-col gap-2 sm:flex-row">
                  {QUALITIES[kind].map(({ id, label, hint }) => <Choice key={id} selected={quality === id} onClick={() => setQuality(id)} title={label} hint={hint} />)}
                </div>
              </fieldset>
              {make.error && <p className="flex items-start gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {errorText(make.error)}</p>}
            </DialogBody>
            <DialogFooter className="justify-end">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button disabled={!count || rangeBad || make.isPending} onClick={() => make.mutate()}>
                {make.isPending ? <Loader2 className="animate-spin" /> : <Icon />} Make {kind.toUpperCase()} of {plural(count, "page")}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogBody className="flex flex-col items-center gap-3 py-8 text-center" aria-live="polite">
              {status === "failed" ? <TriangleAlert className="size-8 text-destructive" /> : status === "ready" ? <Check className="size-8 text-success" /> : <Loader2 className="size-8 animate-spin text-primary" />}
              <p className="font-medium">{status === "failed" ? "It couldn't be made" : status === "ready" ? "Your download has started" : `Making the ${kind.toUpperCase()}…`}</p>
              <p className="text-sm text-muted-foreground">
                {status === "failed" ? current?.error : status === "ready" ? `${current?.filename}${current?.bytes ? ` · ${formatBytes(current.bytes)}` : ""}` : `${plural(count, "page")}. It downloads by itself when it is ready, and you can close this: it will wait in the list on the scan's page.`}
              </p>
            </DialogBody>
            <DialogFooter className="justify-end">
              {status === "ready" && <Button variant="secondary" asChild><a href={job.url} download><Download /> Download again</a></Button>}
              <Button onClick={onClose}>Close</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
