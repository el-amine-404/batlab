import { Loader2, Maximize, RotateCcw, X } from "lucide-react";
import { Dialog as Primitive } from "radix-ui";
import { type KeyboardEvent, type PointerEvent, useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { Page, PageEdit, Point } from "@/lib/api";

const WHOLE: Point[] = [[0, 0], [1, 0], [1, 1], [0, 1]];
const NAMES = ["top left", "top right", "bottom right", "bottom left"];
const clamp = (value: number) => Math.max(-0.05, Math.min(1.05, value));

/** The original photo and the four corners of the page in it, on the whole screen. */
export function CropEditor({ page, onClose, onSave }: { page: Page; onClose: () => void; onSave: (edit: PageEdit) => void }) {
  const initial = page.corners ?? WHOLE;
  const [corners, setCorners] = useState<Point[]>(initial);
  const [radius, setRadius] = useState(page.corner_radius ?? 0);
  const [natural, setNatural] = useState<[number, number] | null>(null);
  const [room, setRoom] = useState<[number, number]>([0, 0]);
  const [dragging, setDragging] = useState<number | null>(null);
  const stage = useRef<SVGSVGElement>(null);
  const source = page.source_url ?? page.preview_url ?? "";

  // A callback: the dialog's content isn't there yet when this component's first effects run.
  const observer = useRef<ResizeObserver | null>(null);
  const area = useCallback((element: HTMLDivElement | null) => {
    observer.current?.disconnect();
    if (!element) return;
    setRoom([element.clientWidth, element.clientHeight]);
    observer.current = new ResizeObserver(() => setRoom([element.clientWidth, element.clientHeight]));
    observer.current.observe(element);
  }, []);

  const margin = 28;
  const scale = natural ? Math.max(0, Math.min((room[0] - margin * 2) / natural[0], (room[1] - margin * 2) / natural[1])) : 0;
  const width = natural ? Math.max(1, natural[0] * scale) : 0;
  const height = natural ? Math.max(1, natural[1] * scale) : 0;

  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (dragging === null || !stage.current) return;
    const box = stage.current.getBoundingClientRect();
    const point: Point = [clamp((event.clientX - box.left) / box.width), clamp((event.clientY - box.top) / box.height)];
    setCorners((list) => list.map((value, index) => (index === dragging ? point : value)));
  };
  const nudge = (index: number) => (event: KeyboardEvent) => {
    const step = event.shiftKey ? 0.01 : 0.002;
    const by: Record<string, Point> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const delta = by[event.key];
    if (!delta) return;
    event.preventDefault();
    setCorners((list) => list.map((value, at) => (at === index ? [clamp(value[0] + delta[0]), clamp(value[1] + delta[1])] : value)));
  };

  const shape = corners.map(([x, y]) => `${x * width},${y * height}`).join(" ");
  const active = dragging === null ? null : corners[dragging];
  // The magnifier keeps out from under the finger: it sits in the far top corner.
  const loupeLeft = active && active[0] > 0.5;
  const LOUPE = 136, ZOOM = 3;

  return (
    <Primitive.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Primitive.Portal>
        <Primitive.Content aria-describedby={undefined} className="fixed inset-0 z-[65] flex flex-col bg-background outline-none">
          <header className="flex shrink-0 items-center gap-3 border-b border-border px-3 py-2 pt-[max(.5rem,env(safe-area-inset-top))]">
            <Primitive.Close asChild><Button variant="ghost" size="icon" aria-label="Close without changing"><X /></Button></Primitive.Close>
            <div className="min-w-0 flex-1">
              <Primitive.Title className="text-sm font-semibold">Adjust crop</Primitive.Title>
              <p className="truncate text-xs text-muted-foreground max-sm:hidden">Drag a corner onto the page's corner. Arrow keys move a selected corner; with Shift, further.</p>
            </div>
            <Button variant="secondary" size="sm" onClick={() => setCorners(WHOLE)}><Maximize /> <span className="max-sm:hidden">Whole photo</span></Button>
            <Button variant="secondary" size="sm" onClick={() => { setCorners(initial); setRadius(page.corner_radius ?? 0); }}><RotateCcw /> <span className="max-sm:hidden">Undo changes</span></Button>
          </header>
          <div ref={area} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-black/40">
            {!natural && <Loader2 className="absolute size-6 animate-spin text-muted-foreground" aria-label="Loading the photo" />}
            <div className="relative" style={{ width, height }}>
              <img src={source} alt="The original photo" draggable={false} className="size-full select-none"
                   onLoad={(event) => setNatural([event.currentTarget.naturalWidth, event.currentTarget.naturalHeight])} />
              {natural && (
                <svg ref={stage} viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 size-full touch-none overflow-visible"
                     onPointerMove={move} onPointerUp={() => setDragging(null)} onPointerCancel={() => setDragging(null)}>
                  <path fillRule="evenodd" fill="rgba(0,0,0,.55)" d={`M0 0H${width}V${height}H0Z M${corners.map(([x, y]) => `${x * width} ${y * height}`).join("L")}Z`} />
                  <polygon points={shape} fill="none" stroke="#5eead4" strokeWidth="2" />
                  {corners.map(([x, y], index) => (
                    <g key={NAMES[index]} transform={`translate(${x * width} ${y * height})`} role="slider" tabIndex={0} aria-label={`The ${NAMES[index]} corner`}
                       aria-valuetext={`${Math.round(x * 100)}% across, ${Math.round(y * 100)}% down`} aria-valuenow={Math.round(x * 100)}
                       className="cursor-grab outline-none active:cursor-grabbing [&:focus-visible>.ring]:opacity-100" onKeyDown={nudge(index)}
                       onPointerDown={(event) => { event.preventDefault(); (event.currentTarget as Element).closest("svg")!.setPointerCapture(event.pointerId); setDragging(index); }}>
                      <circle r="26" fill="transparent" />
                      <circle className="ring opacity-0" r="17" fill="none" stroke="#fff" strokeWidth="2" />
                      <circle r="11" fill="rgba(45,212,191,.28)" stroke="#5eead4" strokeWidth="2.5" />
                      <circle r="2" fill="#fff" />
                    </g>
                  ))}
                </svg>
              )}
            </div>
            {active && (
              <div aria-hidden className="pointer-events-none absolute top-3 overflow-hidden rounded-full border-2 border-white bg-black shadow-2xl" style={{ width: LOUPE, height: LOUPE, [loupeLeft ? "left" : "right"]: 12 }}>
                <img src={source} alt="" className="absolute max-w-none" style={{ width: width * ZOOM, height: height * ZOOM, left: LOUPE / 2 - active[0] * width * ZOOM, top: LOUPE / 2 - active[1] * height * ZOOM }} />
                <span className="absolute top-1/2 left-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary" />
              </div>
            )}
          </div>
          <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 pb-[max(.75rem,env(safe-area-inset-bottom))]">
            <label className="flex min-w-48 items-center gap-3 text-sm">
              <span className="shrink-0">Rounded corners</span>
              <input type="range" min="0" max="0.25" step="0.01" value={radius} onChange={(event) => setRadius(Number(event.target.value))} className="w-28 accent-[var(--primary)]" />
              <output className="w-9 text-muted-foreground tabular-nums">{Math.round(radius * 100)}%</output>
            </label>
            <div className="flex gap-2 max-sm:w-full">
              <Button variant="secondary" className="max-sm:flex-1" onClick={onClose}>Cancel</Button>
              <Button className="max-sm:flex-1" onClick={() => onSave({ corners, corner_radius: radius })}>Apply</Button>
            </div>
          </footer>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
