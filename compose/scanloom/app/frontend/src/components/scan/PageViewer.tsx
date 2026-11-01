import { ChevronLeft, ChevronRight, Crop, Download, Loader2, RotateCcw, RotateCw, Trash2, TriangleAlert, X, ZoomIn, ZoomOut } from "lucide-react";
import { Dialog as Primitive } from "radix-ui";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Appearance, Page, PageEdit } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";

import { APPEARANCES, STAGE, TIMINGS } from "./labels";

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <div>
        <h3 className="text-xs font-semibold tracking-wide text-foreground uppercase">{title}</h3>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {children}
    </section>
  );
}

/** One page, large, with everything that can be done to it. Arrow keys or a swipe for the next. */
export function PageViewer({ pages, at, onGo, onClose, onEdit, onCrop, onRemove, onRetry }: {
  pages: Page[]; at: number; onGo: (index: number) => void; onClose: () => void;
  onEdit: (page: Page, edit: PageEdit) => void; onCrop: (page: Page) => void; onRemove: (page: Page) => void; onRetry: (page: Page) => void;
}) {
  const page = pages[at];
  const [zoomed, setZoomed] = useState(false);
  const swipe = useRef<number | null>(null);
  useEffect(() => setZoomed(false), [page?.id]);
  useEffect(() => {
    const next = pages[at + 1], previous = pages[at - 1];
    for (const near of [next, previous]) if (near?.preview_url) new Image().src = near.preview_url;
  }, [at, pages]);
  if (!page) return null;
  const isWorking = !["ready", "failed", "blocked"].includes(page.status);
  const total = Object.values(page.timings).reduce((sum, value) => sum + value, 0);

  return (
    <Primitive.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Primitive.Portal>
        <Primitive.Content aria-describedby={undefined} className="fixed inset-0 z-[55] flex flex-col bg-background outline-none md:flex-row"
          onKeyDown={(event) => {
            if (event.target instanceof HTMLElement && event.target.closest("[role=listbox],[role=combobox]")) return;
            if (event.key === "ArrowRight" && at < pages.length - 1) onGo(at + 1);
            if (event.key === "ArrowLeft" && at > 0) onGo(at - 1);
          }}>
          <div className="relative flex min-h-0 flex-1 flex-col bg-black/40"
               onTouchStart={(event) => { swipe.current = zoomed || event.touches.length > 1 ? null : event.touches[0].clientX; }}
               onTouchEnd={(event) => {
                 if (swipe.current === null) return;
                 const moved = event.changedTouches[0].clientX - swipe.current;
                 if (moved < -60 && at < pages.length - 1) onGo(at + 1);
                 if (moved > 60 && at > 0) onGo(at - 1);
               }}>
            <div className="flex shrink-0 items-center gap-2 px-3 py-2 pt-[max(.5rem,env(safe-area-inset-top))]">
              <Primitive.Close asChild><Button variant="secondary" size="icon" aria-label="Close"><X /></Button></Primitive.Close>
              <Primitive.Title className="min-w-0 flex-1 truncate text-sm font-medium">Page {at + 1} of {pages.length}</Primitive.Title>
              <Button variant="secondary" size="icon" aria-pressed={zoomed} aria-label={zoomed ? "Fit to the screen" : "Actual size"} onClick={() => setZoomed(!zoomed)}>{zoomed ? <ZoomOut /> : <ZoomIn />}</Button>
            </div>
            <div className={cn("relative min-h-0 flex-1", zoomed ? "overflow-auto" : "overflow-hidden")}>
              {page.preview_url ? (
                <img key={page.preview_url} src={page.preview_url} alt={`Page ${at + 1}`} className={cn(zoomed ? "max-w-none" : "absolute inset-0 size-full object-contain p-3", isWorking && "opacity-50")} />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">{STAGE[page.status]}</div>
              )}
              {isWorking && <div className="pointer-events-none absolute inset-0 flex items-center justify-center"><span className="flex items-center gap-2 rounded-full bg-black/80 px-4 py-2 text-sm text-white"><Loader2 className="size-4 animate-spin" /> {STAGE[page.status]}</span></div>}
            </div>
            <Button variant="secondary" size="icon" aria-label="Previous page" disabled={at === 0} className="absolute top-1/2 left-3 size-11 rounded-full disabled:invisible" onClick={() => onGo(at - 1)}><ChevronLeft /></Button>
            <Button variant="secondary" size="icon" aria-label="Next page" disabled={at === pages.length - 1} className="absolute top-1/2 right-3 size-11 rounded-full disabled:invisible" onClick={() => onGo(at + 1)}><ChevronRight /></Button>
          </div>

          <aside className="flex max-h-[45dvh] shrink-0 flex-col gap-4 overflow-y-auto border-t border-border bg-card p-4 pb-[max(1rem,env(safe-area-inset-bottom))] md:max-h-none md:w-80 md:border-t-0 md:border-l">
            <div>
              <h2 className="text-sm font-semibold [overflow-wrap:anywhere]">{page.original_name}</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">{page.width && page.height ? `${page.width} × ${page.height} px` : STAGE[page.status]}</p>
            </div>
            {page.error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                <p className="flex items-start gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {page.error}</p>
                {page.status === "failed" && <Button variant="secondary" size="sm" className="mt-2" onClick={() => onRetry(page)}><RotateCw /> Try again</Button>}
              </div>
            )}
            {page.warnings.length > 0 && (
              <ul className="flex flex-col gap-1.5 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
                {page.warnings.map((warning) => <li key={warning} className="flex items-start gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {warning}</li>)}
              </ul>
            )}
            <Section title="Rotate" hint="Turns the page a quarter turn.">
              <div className="grid grid-cols-2 gap-2">
                <Button variant="secondary" title="A quarter turn to the left" disabled={isWorking} onClick={() => onEdit(page, { rotate_by: 270 })}><RotateCcw /> Rotate left</Button>
                <Button variant="secondary" title="A quarter turn to the right" disabled={isWorking} onClick={() => onEdit(page, { rotate_by: 90 })}><RotateCw /> Rotate right</Button>
              </div>
            </Section>
            <Section title="Crop" hint="Where the page's corners are in the photo.">
              <Button variant="secondary" className="w-full" disabled={!page.source_url} title={page.source_url ? undefined : "The photo is still being prepared"} onClick={() => onCrop(page)}><Crop /> Adjust the corners</Button>
            </Section>
            <Section title="Look" hint="Transforms this page only. It can be changed back.">
              <Select value={page.appearance} disabled={isWorking} onValueChange={(value) => onEdit(page, { appearance: value as Appearance })}>
                <SelectTrigger aria-label="Look of this page"><SelectValue /></SelectTrigger>
                <SelectContent className="z-[60]">{APPEARANCES.map(({ id, label }) => <SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent>
              </Select>
            </Section>
            {page.png_url && (
              <Section title="Download this page" hint="For several pages, use PDF or ZIP on the scan.">
                <div className="flex flex-col gap-2">
                  <Button asChild><a href={page.png_url} download><Download /> Lossless PNG{page.result_bytes ? ` · ${formatBytes(page.result_bytes)}` : ""}</a></Button>
                  {page.jpeg_url && <Button asChild variant="secondary"><a href={page.jpeg_url} download><Download /> JPEG{page.jpeg_bytes ? ` · ${formatBytes(page.jpeg_bytes)}` : ""}</a></Button>}
                </div>
              </Section>
            )}
            {total > 0 && (
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer select-none hover:text-foreground">Done in {total.toFixed(1)} s</summary>
                <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 tabular-nums">
                  {Object.entries(page.timings).map(([name, seconds]) => (
                    <div key={name} className="contents"><dt>{TIMINGS[name] ?? name}</dt><dd>{seconds.toFixed(1)} s</dd></div>
                  ))}
                </dl>
              </details>
            )}
            <Button variant="destructive" className="mt-auto" onClick={() => onRemove(page)}><Trash2 /> Delete this page</Button>
          </aside>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
