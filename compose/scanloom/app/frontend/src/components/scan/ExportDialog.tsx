import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Download, FileArchive, FileText, Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type ExportRequest, type Project } from "@/lib/api";
import { describePages, parsePages } from "@/lib/pageSpec";
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

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A PDF or a ZIP of all the pages, those named, or those selected; made, then downloaded by itself. */
export function ExportDialog({ project, initial, selected, onClose }: { project: Project; initial: Kind; selected: string[]; onClose: () => void }) {
  const client = useQueryClient();
  const ready = project.completed_count;
  const [kind, setKind] = useState<Kind>(initial);
  const [quality, setQuality] = useState<Quality>("balanced");
  const [which, setWhich] = useState<"all" | "some" | "selected">(selected.length ? "selected" : "all");
  const [spec, setSpec] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [filename, setFilename] = useState(project.title);
  const [color, setColor] = useState<NonNullable<ExportRequest["color"]>>("keep");
  const [paper, setPaper] = useState<NonNullable<ExportRequest["paper"]>>("fit");
  const [margin, setMargin] = useState("0");
  const [longEdge, setLongEdge] = useState("");
  const [jpegQuality, setJpegQuality] = useState("");
  const [job, setJob] = useState<{ id: string; url: string; count: number } | null>(null);
  const started = useRef(false);

  const parsed = useMemo(() => (which === "some" ? parsePages(spec, project.page_count) : null), [which, spec, project.page_count]);
  const current = job && project.exports.find((item) => item.id === job.id);
  const status = current?.status ?? (job ? "queued" : null);
  const lossless = quality === "lossless";

  const numbers = { longEdge: Number(longEdge), jpeg: Number(jpegQuality), margin: Number(margin) };
  const problem = (longEdge && !(numbers.longEdge >= 300 && numbers.longEdge <= 12000)) ? "The largest side is 300 to 12000 pixels."
    : (jpegQuality && !lossless && !(numbers.jpeg >= 30 && numbers.jpeg <= 100)) ? "JPEG quality is 30 to 100."
    : (kind === "pdf" && paper !== "fit" && !(numbers.margin >= 0 && numbers.margin <= 50)) ? "The margin is 0 to 50 mm." : null;

  const make = useMutation({
    mutationFn: () => api.createExport(project.id, {
      kind, quality, filename: filename.trim() || undefined, color,
      ...(longEdge ? { long_edge: numbers.longEdge } : {}),
      ...(jpegQuality && !lossless ? { jpeg_quality: numbers.jpeg } : {}),
      ...(kind === "pdf" && paper !== "fit" ? { paper, margin_mm: numbers.margin } : {}),
      ...(which === "some" ? { pages: spec } : which === "selected" ? { page_ids: selected } : {}),
    }),
    onSuccess: (answer) => {
      setJob({ id: answer.id, url: answer.download_url, count: answer.page_count });
      started.current = false;
      void client.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });

  // While it is being made, the stream says when it is ready; this is in case the stream can't.
  useEffect(() => {
    if (!job || status === "ready" || status === "failed") return;
    const timer = window.setInterval(() => void client.invalidateQueries({ queryKey: ["project", project.id] }), 2000);
    return () => window.clearInterval(timer);
  }, [job, status, client, project.id]);

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

  const count = which === "all" ? ready : which === "selected" ? selected.length : parsed?.pages?.length ?? 0;
  const Icon = kind === "pdf" ? FileText : FileArchive;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="truncate">Download {project.title}</DialogTitle>
          <DialogDescription className="sr-only">Choose what to download.</DialogDescription>
        </DialogHeader>
        {!job ? (
          <>
            <DialogBody className="flex flex-col gap-5">
              <fieldset>
                <legend className="mb-2 text-sm font-medium">As</legend>
                <div role="radiogroup" className="flex gap-2">
                  <Choice selected={kind === "pdf"} onClick={() => setKind("pdf")} title="PDF" hint="One document" />
                  <Choice selected={kind === "zip"} onClick={() => { setKind("zip"); if (quality === "small") setQuality("balanced"); }} title="ZIP" hint="One image per page" />
                </div>
              </fieldset>
              <fieldset>
                <legend className="mb-2 text-sm font-medium">Pages</legend>
                <div role="radiogroup" className="flex flex-wrap gap-2">
                  <Choice selected={which === "all"} onClick={() => setWhich("all")} title={`All ${ready}`} />
                  <Choice selected={which === "some"} onClick={() => setWhich("some")} title="Choose by number" />
                  {selected.length > 0 && <Choice selected={which === "selected"} onClick={() => setWhich("selected")} title={`${selected.length} selected`} />}
                </div>
                {which === "some" && (
                  <div className="mt-2 flex flex-col gap-1">
                    <Input autoFocus aria-label="Page numbers" aria-invalid={Boolean(spec && parsed?.error)} placeholder={`1-3,6,7-${Math.max(project.page_count, 9)}`} inputMode="text" value={spec} onChange={(event) => setSpec(event.target.value)} />
                    {spec && parsed?.error ? <p className="text-xs text-destructive" role="alert">{parsed.error}</p>
                      : parsed?.pages ? <p className="text-xs text-muted-foreground" role="status">{plural(parsed.pages.length, "page")}, in this order: {describePages(parsed.pages)}</p>
                      : <p className="text-xs text-muted-foreground">Pages and ranges with commas. They come in the order written; 9-7 runs backwards.</p>}
                  </div>
                )}
              </fieldset>
              <fieldset>
                <legend className="mb-2 text-sm font-medium">Quality</legend>
                <div role="radiogroup" className="flex flex-col gap-2 sm:flex-row">
                  {QUALITIES[kind].map(({ id, label, hint }) => <Choice key={id} selected={quality === id} onClick={() => setQuality(id)} title={label} hint={hint} />)}
                </div>
              </fieldset>
              <div>
                <button type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)} className="flex min-h-8 items-center gap-1 rounded text-sm font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring">
                  <ChevronDown className={cn("size-4 transition-transform", advanced && "rotate-180")} /> Advanced options
                </button>
                {advanced && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <Field id="filename" label="File name"><Input id="filename" maxLength={160} value={filename} onChange={(event) => setFilename(event.target.value)} /></Field>
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-sm font-medium">Colour</span>
                      <Select value={color} onValueChange={(value) => setColor(value as typeof color)}>
                        <SelectTrigger aria-label="Colour"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="keep">As the pages are</SelectItem>
                          <SelectItem value="gray">Grayscale</SelectItem>
                          <SelectItem value="mono">Black &amp; white</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <Field id="edge" label="Largest side (pixels)" hint="Never enlarges. Empty: as the quality says.">
                      <Input id="edge" type="number" inputMode="numeric" min={300} max={12000} placeholder={lossless ? "Full size" : kind === "pdf" ? (quality === "small" ? "1600" : "2400") : "Full size"} value={longEdge} onChange={(event) => setLongEdge(event.target.value)} />
                    </Field>
                    {!lossless && (
                      <Field id="jpeg" label="JPEG quality" hint="30 to 100. Higher is sharper and larger.">
                        <Input id="jpeg" type="number" inputMode="numeric" min={30} max={100} placeholder={kind === "pdf" ? (quality === "small" ? "74" : "88") : "92"} value={jpegQuality} onChange={(event) => setJpegQuality(event.target.value)} />
                      </Field>
                    )}
                    {kind === "pdf" && (
                      <>
                        <div className="flex flex-col gap-1">
                          <span className="text-sm font-medium">Paper</span>
                          <Select value={paper} onValueChange={(value) => setPaper(value as typeof paper)}>
                            <SelectTrigger aria-label="Paper"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="fit">Each page its own size</SelectItem>
                              <SelectItem value="a4">A4</SelectItem>
                              <SelectItem value="a5">A5</SelectItem>
                              <SelectItem value="letter">US Letter</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        {paper !== "fit" && (
                          <Field id="margin" label="Margin (mm)" hint="Pages turn to fit the paper.">
                            <Input id="margin" type="number" inputMode="decimal" min={0} max={50} value={margin} onChange={(event) => setMargin(event.target.value)} />
                          </Field>
                        )}
                      </>
                    )}
                    {problem && <p className="text-xs text-destructive sm:col-span-2" role="alert">{problem}</p>}
                  </div>
                )}
              </div>
              {make.error && <p className="flex items-start gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {errorText(make.error)}</p>}
            </DialogBody>
            <DialogFooter className="justify-end">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button disabled={!count || Boolean(problem) || make.isPending} onClick={() => make.mutate()}>
                {make.isPending ? <Loader2 className="animate-spin" /> : <Icon />} Make {kind.toUpperCase()}{count ? ` of ${plural(count, "page")}` : ""}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogBody className="flex flex-col items-center gap-3 py-8 text-center" aria-live="polite">
              {status === "failed" ? <TriangleAlert className="size-8 text-destructive" /> : status === "ready" ? <Check className="size-8 text-success" /> : <Loader2 className="size-8 animate-spin text-primary" />}
              <p className="font-medium">{status === "failed" ? "It couldn't be made" : status === "ready" ? "Your download has started" : `Making the ${kind.toUpperCase()}…`}</p>
              <p className="text-sm text-muted-foreground">
                {status === "failed" ? current?.error : status === "ready" ? `${current?.filename}${current?.bytes ? ` · ${formatBytes(current.bytes)}` : ""}` : `${plural(job.count, "page")}. It downloads by itself when it is ready, and you can close this: it will wait in the scan's ⋮ menu.`}
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
