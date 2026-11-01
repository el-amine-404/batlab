import { Grid3x3, Loader2, Magnet, Maximize, Minus, Plus, RotateCcw, ScanSearch, Scan, TriangleAlert, X } from "lucide-react";
import { Dialog as Primitive } from "radix-ui";
import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useRef, useState, type WheelEvent } from "react";

import { Button } from "@/components/ui/button";
import type { Page, PageEdit, Point } from "@/lib/api";

import { cn } from "@/lib/utils";

import { cornerField, type CornerField, isValidQuad, previewMatrix, snapToCorner, thirds } from "./quad";

const WHOLE: Point[] = [[0, 0], [1, 0], [1, 1], [0, 1]];
const NAMES = ["top left", "top right", "bottom right", "bottom left"];
const SIDES = ["top", "right", "bottom", "left"];
const clamp = (value: number) => Math.max(-0.05, Math.min(1.05, value));
const LOUPE = 136, ZOOM = 3, MARGIN = 28;
const PREVIEW_LONG = 280;
const MAX_ZOOM = 10;
const SNAP_PIXELS = 18; // how far, on screen, a dropped corner looks for a real one
const FIELD_LONG = 640;

type Grab = { corner: number } | { side: number; from: [number, number]; start: Point[] };

/**
 * The original photo and the outline of the page in it, on the whole screen. Drag a
 * corner, or a side to move its two corners together; a magnifier shows the spot under
 * the finger, and beside the photo is what the page will look like, straightened.
 * Wheel, pinch or the buttons zoom; dragging the photo moves it. Guides show straight
 * lines to judge by, and a dropped corner snaps to a real corner close by.
 */
export function CropEditor({ page, onClose, onSave }: { page: Page; onClose: () => void; onSave: (edit: PageEdit) => void }) {
  const initial = page.corners ?? WHOLE;
  const [corners, setCorners] = useState<Point[]>(initial);
  const [natural, setNatural] = useState<[number, number] | null>(null);
  const [room, setRoom] = useState<[number, number]>([0, 0]);
  const [grab, setGrab] = useState<Grab | null>(null);
  const stage = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<[number, number]>([0, 0]);
  const [guides, setGuides] = useState(true);
  const [snap, setSnap] = useState(true);
  const field = useRef<CornerField | null>(null);
  const fingers = useRef(new Map<number, [number, number]>());
  const gesture = useRef<{ dist: number; zoom: number; pan: [number, number]; mid: [number, number] } | { from: [number, number]; pan: [number, number] } | null>(null);
  const areaEl = useRef<HTMLDivElement | null>(null);
  const source = page.source_url ?? page.preview_url ?? "";

  // A callback: the dialog's content isn't there yet when this component's first effects run.
  const observer = useRef<ResizeObserver | null>(null);
  const area = useCallback((element: HTMLDivElement | null) => {
    observer.current?.disconnect();
    areaEl.current = element;
    if (!element) return;
    setRoom([element.clientWidth, element.clientHeight]);
    observer.current = new ResizeObserver(() => setRoom([element.clientWidth, element.clientHeight]));
    observer.current.observe(element);
  }, []);

  const fit = natural ? Math.max(0, Math.min((room[0] - MARGIN * 2) / natural[0], (room[1] - MARGIN * 2) / natural[1])) : 0;
  const scale = fit * zoom;
  const width = natural ? natural[0] * scale : 0;
  const height = natural ? natural[1] * scale : 0;
  const valid = isValidQuad(corners);
  const changed = JSON.stringify(corners) !== JSON.stringify(initial);

  /** Within reach of the photo: it can't be lost off the screen. */
  const bound = (next: [number, number], z: number): [number, number] => {
    if (!natural) return [0, 0];
    const w = natural[0] * fit * z, h = natural[1] * fit * z;
    const limit = (value: number, size: number, have: number) => { const most = Math.max(0, (size - have) / 2 + 80); return Math.max(-most, Math.min(most, value)); };
    return [limit(next[0], w, room[0]), limit(next[1], h, room[1])];
  };
  /** Zoom keeping the spot at (x, y), measured from the area's middle, where it is. */
  const zoomAt = (next: number, x = 0, y = 0, from = zoom, base = pan) => {
    const z = Math.max(1, Math.min(MAX_ZOOM, next));
    const ratio = z / from;
    setZoom(z);
    setPan(bound([x - (x - base[0]) * ratio, y - (y - base[1]) * ratio], z));
  };
  const fromMiddle = (clientX: number, clientY: number): [number, number] => {
    const box = areaEl.current!.getBoundingClientRect();
    return [clientX - box.left - box.width / 2, clientY - box.top - box.height / 2];
  };
  const wheel = (event: WheelEvent) => {
    const [x, y] = fromMiddle(event.clientX, event.clientY);
    zoomAt(zoom * Math.exp(-event.deltaY * 0.002), x, y);
  };
  const areaDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    fingers.current.set(event.pointerId, [event.clientX, event.clientY]);
    const points = [...fingers.current.values()];
    if (points.length === 2) {
      gesture.current = { dist: Math.hypot(points[0][0] - points[1][0], points[0][1] - points[1][1]), zoom, pan, mid: fromMiddle((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2) };
    } else {
      gesture.current = { from: [event.clientX, event.clientY], pan };
    }
  };
  const areaMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!fingers.current.has(event.pointerId)) return;
    fingers.current.set(event.pointerId, [event.clientX, event.clientY]);
    const now = gesture.current;
    if (!now) return;
    if ("dist" in now) {
      const points = [...fingers.current.values()];
      if (points.length < 2) return;
      const dist = Math.hypot(points[0][0] - points[1][0], points[0][1] - points[1][1]);
      zoomAt(now.zoom * (dist / now.dist), now.mid[0], now.mid[1], now.zoom, now.pan);
    } else {
      setPan(bound([now.pan[0] + event.clientX - now.from[0], now.pan[1] + event.clientY - now.from[1]], zoom));
    }
  };
  const areaUp = (event: PointerEvent<HTMLDivElement>) => {
    fingers.current.delete(event.pointerId);
    gesture.current = null;
  };
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "+" || event.key === "=") zoomAt(zoom * 1.4);
      if (event.key === "-") zoomAt(zoom / 1.4);
      if (event.key === "0") { setZoom(1); setPan([0, 0]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const measure = (image: HTMLImageElement) => {
    setNatural([image.naturalWidth, image.naturalHeight]);
    const ratio = Math.min(1, FIELD_LONG / Math.max(image.naturalWidth, image.naturalHeight));
    const w = Math.round(image.naturalWidth * ratio), h = Math.round(image.naturalHeight * ratio);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return;
    context.drawImage(image, 0, 0, w, h);
    const pixels = context.getImageData(0, 0, w, h).data;
    const gray = new Uint8ClampedArray(w * h);
    for (let i = 0; i < w * h; i += 1) gray[i] = 0.299 * pixels[i * 4] + 0.587 * pixels[i * 4 + 1] + 0.114 * pixels[i * 4 + 2];
    field.current = cornerField(gray, w, h);
  };
  const drop = () => {
    if (grab && "corner" in grab && snap && field.current && natural) {
      const reach = (SNAP_PIXELS / Math.max(width, height)) * Math.max(field.current.width, field.current.height);
      const index = grab.corner;
      setCorners((list) => list.map((value, i) => (i === index ? snapToCorner(field.current!, value, reach) : value)));
    }
    setGrab(null);
  };

  const at = (event: PointerEvent): Point => {
    const box = stage.current!.getBoundingClientRect();
    return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height];
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (!grab || !stage.current) return;
    const point = at(event);
    if ("corner" in grab) {
      setCorners((list) => list.map((value, index) => (index === grab.corner ? [clamp(point[0]), clamp(point[1])] : value)));
    } else {
      // A side moves straight out or in, its corners along their neighbours' lines left to the hand.
      const dx = point[0] - grab.from[0], dy = point[1] - grab.from[1];
      const pair = [grab.side, (grab.side + 1) % 4];
      setCorners(grab.start.map((value, index) => (pair.includes(index) ? [clamp(value[0] + dx), clamp(value[1] + dy)] : value)));
    }
  };
  const take = (event: PointerEvent, next: (svgPoint: Point) => Grab) => {
    event.preventDefault();
    event.stopPropagation();
    stage.current!.setPointerCapture(event.pointerId);
    setGrab(next(at(event)));
  };
  const nudge = (index: number) => (event: KeyboardEvent) => {
    const step = event.shiftKey ? 0.01 : 0.002;
    const by: Record<string, Point> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const delta = by[event.key];
    if (!delta) return;
    event.preventDefault();
    setCorners((list) => list.map((value, i) => (i === index ? [clamp(value[0] + delta[0]), clamp(value[1] + delta[1])] : value)));
  };

  const active: Point | null = grab && "corner" in grab ? corners[grab.corner] : null;
  const loupeLeft = active && active[0] > 0.5;
  const preview = natural && valid ? previewMatrix(corners, natural, PREVIEW_LONG) : null;

  return (
    <Primitive.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Primitive.Portal>
        <Primitive.Content aria-describedby={undefined} className="fixed inset-0 z-[65] flex flex-col bg-background outline-none">
          <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 pt-[max(.5rem,env(safe-area-inset-top))]">
            <Primitive.Close asChild><Button variant="ghost" size="icon" aria-label="Close without changing"><X /></Button></Primitive.Close>
            <div className="min-w-0 flex-1 basis-40">
              <Primitive.Title className="text-sm font-semibold">Adjust the corners</Primitive.Title>
              <p className="truncate text-xs text-muted-foreground max-sm:hidden">Drag a corner or a side. Scroll or pinch to zoom, drag the photo to move it.</p>
            </div>
            <Button variant="secondary" size="sm" title="Forget this crop and let Scanloom find the page again" onClick={() => onSave({ redetect: true })}><ScanSearch /> Find again</Button>
            <Button variant="secondary" size="sm" title="The whole photo, no crop" onClick={() => setCorners(WHOLE)}><Maximize /> Whole photo</Button>
            <Button variant="secondary" size="sm" title="Back to where the corners were" disabled={!changed} onClick={() => setCorners(initial)}><RotateCcw /> Undo</Button>
          </header>
          <div className="flex min-h-0 flex-1">
            <div ref={area} onWheel={wheel} onPointerDown={areaDown} onPointerMove={areaMove} onPointerUp={areaUp} onPointerCancel={areaUp}
                 className="relative flex min-h-0 min-w-0 flex-1 cursor-grab touch-none items-center justify-center overflow-hidden bg-black/40 active:cursor-grabbing">
              {!natural && <Loader2 className="absolute size-6 animate-spin text-muted-foreground" aria-label="Loading the photo" />}
              <div className="relative shrink-0" style={natural ? { width, height, transform: `translate(${pan[0]}px, ${pan[1]}px)` } : { width: 1, height: 1, overflow: "hidden" }}>
                <img src={source} alt="The original photo" draggable={false} className="size-full select-none"
                     onLoad={(event) => measure(event.currentTarget)} />
                {natural && width > 0 && (
                  <svg ref={stage} viewBox={`0 0 ${width} ${height}`} className="pointer-events-none absolute inset-0 size-full touch-none overflow-visible"
                       onPointerMove={move} onPointerUp={drop} onPointerCancel={() => setGrab(null)}>
                    <path fillRule="evenodd" fill="rgba(0,0,0,.55)" d={`M0 0H${width}V${height}H0Z M${corners.map(([x, y]) => `${x * width} ${y * height}`).join("L")}Z`} />
                    {guides && valid && thirds(corners).map(([a, b], index) => (
                      <line key={index} x1={a[0] * width} y1={a[1] * height} x2={b[0] * width} y2={b[1] * height} stroke="#fff" strokeOpacity=".45" strokeWidth="1" />
                    ))}
                    {/* Each side carried on across the photo: lay it along the page's real edge. */}
                    {guides && corners.map(([x, y], index) => {
                      const [nx, ny] = corners[(index + 1) % 4];
                      const far = 3;
                      return <line key={`side-${index}`} x1={(x - (nx - x) * far) * width} y1={(y - (ny - y) * far) * height} x2={(nx + (nx - x) * far) * width} y2={(ny + (ny - y) * far) * height}
                                   stroke="#5eead4" strokeOpacity=".5" strokeWidth="1" strokeDasharray="6 6" />;
                    })}
                    {guides && active && (
                      <>
                        <line x1={active[0] * width} y1={-4000} x2={active[0] * width} y2={height + 4000} stroke="#fff" strokeOpacity=".7" strokeWidth="1" />
                        <line x1={-4000} y1={active[1] * height} x2={width + 4000} y2={active[1] * height} stroke="#fff" strokeOpacity=".7" strokeWidth="1" />
                      </>
                    )}
                    <polygon points={corners.map(([x, y]) => `${x * width},${y * height}`).join(" ")} fill="none" stroke={valid ? "#5eead4" : "#f87171"} strokeWidth="2" />
                    {corners.map(([x, y], index) => {
                      const [nx, ny] = corners[(index + 1) % 4];
                      const mx = ((x + nx) / 2) * width, my = ((y + ny) / 2) * height;
                      const flat = Math.abs(nx - x) * width > Math.abs(ny - y) * height;
                      return (
                        <g key={SIDES[index]} transform={`translate(${mx} ${my})`} className="pointer-events-auto cursor-move" aria-label={`The ${SIDES[index]} side`}
                           onPointerDown={(event) => take(event, (point) => ({ side: index, from: point as [number, number], start: corners }))}>
                          <circle r="20" fill="transparent" />
                          <rect x={flat ? -12 : -3} y={flat ? -3 : -12} width={flat ? 24 : 6} height={flat ? 6 : 24} rx="3" fill="#5eead4" stroke="#042f2e" strokeWidth="1" />
                        </g>
                      );
                    })}
                    {corners.map(([x, y], index) => (
                      <g key={NAMES[index]} transform={`translate(${x * width} ${y * height})`} role="slider" tabIndex={0} aria-label={`The ${NAMES[index]} corner`}
                         aria-valuetext={`${Math.round(x * 100)}% across, ${Math.round(y * 100)}% down`} aria-valuenow={Math.round(x * 100)}
                         className="pointer-events-auto cursor-grab outline-none active:cursor-grabbing [&:focus-visible>.ring]:opacity-100" onKeyDown={nudge(index)}
                         onPointerDown={(event) => take(event, () => ({ corner: index }))}>
                        <circle r="26" fill="transparent" />
                        <circle className="ring opacity-0" r="17" fill="none" stroke="#fff" strokeWidth="2" />
                        <circle r="11" fill="rgba(45,212,191,.28)" stroke="#5eead4" strokeWidth="2.5" />
                        <circle r="2" fill="#fff" />
                      </g>
                    ))}
                  </svg>
                )}
              </div>
              <div className="absolute bottom-3 left-3 flex items-center gap-1 rounded-lg border border-border bg-popover p-1 shadow-xl" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
                <Button variant="ghost" size="icon-sm" aria-label="Zoom out" title="Zoom out (−)" disabled={zoom <= 1} onClick={() => zoomAt(zoom / 1.4)}><Minus /></Button>
                <output className="w-12 text-center text-xs tabular-nums">{Math.round(zoom * 100)}%</output>
                <Button variant="ghost" size="icon-sm" aria-label="Zoom in" title="Zoom in (+)" disabled={zoom >= MAX_ZOOM} onClick={() => zoomAt(zoom * 1.4)}><Plus /></Button>
                <Button variant="ghost" size="icon-sm" aria-label="Fit the photo" title="Fit the photo (0)" disabled={zoom === 1} onClick={() => { setZoom(1); setPan([0, 0]); }}><Scan /></Button>
                <span className="mx-1 h-5 w-px bg-border" aria-hidden />
                <Button variant="ghost" size="sm" aria-pressed={guides} title="Straight lines to judge the crop by" className={cn(guides && "bg-accent text-foreground")} onClick={() => setGuides(!guides)}><Grid3x3 /> Guides</Button>
                <Button variant="ghost" size="sm" aria-pressed={snap} title="A dropped corner goes to a real corner close by" className={cn(snap && "bg-accent text-foreground")} onClick={() => setSnap(!snap)}><Magnet /> Snap</Button>
              </div>
              {active && (
                <div aria-hidden className="pointer-events-none absolute top-3 overflow-hidden rounded-full border-2 border-white bg-black shadow-2xl" style={{ width: LOUPE, height: LOUPE, [loupeLeft ? "left" : "right"]: 12 }}>
                  <img src={source} alt="" className="absolute max-w-none" style={{ width: width * ZOOM, height: height * ZOOM, left: LOUPE / 2 - active[0] * width * ZOOM, top: LOUPE / 2 - active[1] * height * ZOOM }} />
                  <span className="absolute top-1/2 left-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary" />
                </div>
              )}
            </div>
            <aside className="hidden w-80 shrink-0 flex-col gap-2 border-l border-border bg-card p-4 md:flex">
              <h3 className="text-xs font-semibold tracking-wide uppercase">The page will look like this</h3>
              <div className="flex min-h-48 flex-1 items-start justify-center overflow-hidden pt-2">
                {preview ? (
                  <div className="relative overflow-hidden bg-tile shadow-lg" style={{ width: preview.width, height: preview.height }}>
                    <img src={source} alt="Preview of the straightened page" draggable={false} className="absolute top-0 left-0 max-w-none origin-top-left" style={{ width: natural![0], height: natural![1], transform: preview.transform }} />
                  </div>
                ) : <p className="flex items-center gap-2 text-sm text-destructive"><TriangleAlert className="size-4" /> The sides cross.</p>}
              </div>
              <p className="text-xs text-muted-foreground">Rotation and colours are applied after.</p>
            </aside>
          </div>
          <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-border px-4 py-3 pb-[max(.75rem,env(safe-area-inset-bottom))]">
            {!valid ? <p className="flex items-center gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="size-4 shrink-0" /> The sides cross: move a corner back.</p> : <span />}
            <div className="flex gap-2">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button disabled={!valid || !changed} onClick={() => onSave({ corners })}>Apply</Button>
            </div>
          </footer>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
