import { Grid3x3, Loader2, Lock, Magnet, Maximize, Minus, Plus, Redo2, RotateCcw, ScanSearch, Scan, TriangleAlert, Undo2, X } from "lucide-react";
import { Dialog as Primitive } from "radix-ui";
import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useRef, useState, type WheelEvent } from "react";

import { Button } from "@/components/ui/button";
import type { Page, PageEdit, Point } from "@/lib/api";

import { cn } from "@/lib/utils";

import { cornerField, type CornerField, edgeField, type EdgeField, isValidQuad, magnetSide, previewMatrix, slideCorner, slideSide, snapToCorner, thirds } from "./quad";

const WHOLE: Point[] = [[0, 0], [1, 0], [1, 1], [0, 1]];
const NAMES = ["top left", "top right", "bottom right", "bottom left"];
const SIDES = ["top", "right", "bottom", "left"];
const clamp = (value: number) => Math.max(-0.05, Math.min(1.05, value));
const LOUPE = 136, ZOOM = 3, MARGIN = 28;
const PREVIEW_LONG = 280;
const MAX_ZOOM = 10;
const SNAP_PIXELS = 18; // how far, on screen, a dropped corner looks for a real one
const EDGE_PIXELS = 24; // how far, on screen, a dropped side looks for the page's edge
const FIELD_LONG = 640;
const NUDGES_TOGETHER = 700; // arrow presses this close are one step of undo
const same = (a: Point[], b: Point[]) => JSON.stringify(a) === JSON.stringify(b);

/** `from` and `start` are where the drag, or its latest change of lock, began. */
type Grab = ({ corner: number; along: number | null } | { side: number }) & { from: Point; start: Point[]; locked: boolean };

/**
 * The original photo and the outline of the page in it, on the whole screen. Drag a
 * corner, or a side to move its two corners together; a magnifier shows the spot under
 * the finger, and beside the photo is what the page will look like, straightened.
 * Wheel, pinch or the buttons zoom; dragging the photo moves it. Guides show straight
 * lines to judge by; a dropped corner snaps to a real corner close by, and a dropped side
 * to the page's real edge, which is drawn before you let go. Each change can be undone
 * on its own. Shift, held
 * at any moment of a drag, or the Lock angle button keeps the sides from turning: a side
 * slides straight out or in, and a corner runs along whichever side the drag follows.
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
  const [lockAngle, setLockAngle] = useState(false);
  const pointer = useRef<Point>([0, 0]);
  const field = useRef<CornerField | null>(null);
  const edges = useRef<EdgeField | null>(null);
  // The outline as of the last event, ahead of the render; and the steps either side of it.
  const latest = useRef<Point[]>(initial);
  const before = useRef<Point[]>(initial);
  const lastNudge = useRef<{ index: number; at: number } | null>(null);
  const [past, setPast] = useState<Point[][]>([]);
  const [future, setFuture] = useState<Point[][]>([]);
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
  const changed = !same(corners, initial);

  const put = (next: Point[]) => { latest.current = next; setCorners(next); };
  /** One step of undo: from what the outline was, to where it is now. */
  const remember = (from: Point[]) => { setPast((list) => [...list, from]); setFuture([]); lastNudge.current = null; };
  const edit = (next: Point[]) => { if (same(next, latest.current)) return; remember(latest.current); put(next); };
  const undo = () => {
    if (grab || !past.length) return;
    const now = latest.current; // read here: the update below runs after put has moved it
    setFuture((list) => [now, ...list]);
    setPast(past.slice(0, -1));
    put(past[past.length - 1]);
    lastNudge.current = null;
  };
  const redo = () => {
    if (grab || !future.length) return;
    const now = latest.current;
    setPast((list) => [...list, now]);
    setFuture(future.slice(1));
    put(future[0]);
    lastNudge.current = null;
  };

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
    // Shift can change with the hand standing still: carry on from here, without a jump.
    const shift = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Shift" && grab) relock(grab, lockAngle || event.type === "keydown");
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      shift(event);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); return; }
      if (event.key === "+" || event.key === "=") zoomAt(zoom * 1.4);
      if (event.key === "-") zoomAt(zoom / 1.4);
      if (event.key === "0") { setZoom(1); setPan([0, 0]); }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", shift);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("keyup", shift); };
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
    edges.current = edgeField(gray, w, h);
  };
  /** What the page's real edge would do to a side let go here. */
  const magnet = (list: Point[], side: number, keepAngle: boolean) => {
    if (!snap || !edges.current || !width) return null;
    const reach = (EDGE_PIXELS / Math.max(width, height)) * Math.max(edges.current.width, edges.current.height);
    return magnetSide(edges.current, list, side, reach, keepAngle);
  };
  const drop = () => {
    if (!grab) return;
    let list = latest.current;
    // Only after a real move: a tap on a handle shouldn't shift anything.
    if (!same(list, before.current)) {
      if ("side" in grab) {
        list = magnet(list, grab.side, grab.locked) ?? list;
      } else if (!grab.locked && snap && field.current && natural) {
        // A locked corner stays on its line: snapping would turn the side it was keeping straight.
        const reach = (SNAP_PIXELS / Math.max(width, height)) * Math.max(field.current.width, field.current.height);
        list = list.map((value, i) => (i === grab.corner ? snapToCorner(field.current!, value, reach) : value));
      }
      remember(before.current);
      put(list);
    }
    setGrab(null);
  };

  const at = (event: PointerEvent): Point => {
    const box = stage.current!.getBoundingClientRect();
    return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height];
  };
  /** The same drag from here on, with the lock on or off. Returns the drag to go on with. */
  const relock = (now: Grab, locked: boolean): Grab => {
    if (now.locked === locked) return now;
    const next: Grab = { ...now, locked, from: pointer.current, start: latest.current, ...("corner" in now ? { along: null } : {}) };
    setGrab(next);
    return next;
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (!grab || !stage.current || !natural) return;
    const point = at(event);
    const now = relock(grab, lockAngle || event.shiftKey);
    pointer.current = point;
    const delta: Point = [point[0] - now.from[0], point[1] - now.from[1]];
    if ("corner" in now) {
      if (now.locked) {
        const slid = slideCorner(now.start, now.corner, delta, natural);
        put(slid.corners);
        if (slid.along !== now.along) setGrab({ ...now, along: slid.along });
      } else {
        put(now.start.map((value, index) => (index === now.corner ? [clamp(value[0] + delta[0]), clamp(value[1] + delta[1])] : value)));
      }
    } else if (now.locked) {
      put(slideSide(now.start, now.side, delta, natural));
    } else {
      const pair = [now.side, (now.side + 1) % 4];
      put(now.start.map((value, index) => (pair.includes(index) ? [clamp(value[0] + delta[0]), clamp(value[1] + delta[1])] : value)));
    }
  };
  const take = (event: PointerEvent, what: { corner: number; along: null } | { side: number }) => {
    event.preventDefault();
    event.stopPropagation();
    stage.current!.setPointerCapture(event.pointerId);
    const from = at(event);
    pointer.current = from;
    before.current = latest.current;
    setGrab({ ...what, from, start: latest.current, locked: lockAngle || event.shiftKey });
  };
  const nudge = (index: number) => (event: KeyboardEvent) => {
    const step = event.shiftKey ? 0.01 : 0.002;
    const by: Record<string, Point> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const delta = by[event.key];
    if (!delta) return;
    event.preventDefault();
    // A run of presses on one corner is one step.
    const now = performance.now(), last = lastNudge.current;
    const list = latest.current;
    if (!last || last.index !== index || now - last.at > NUDGES_TOGETHER) remember(list);
    lastNudge.current = { index, at: now };
    put(list.map((value, i) => (i === index ? [clamp(value[0] + delta[0]), clamp(value[1] + delta[1])] : value)));
  };

  const active: Point | null = grab && "corner" in grab ? corners[grab.corner] : null;
  // The magnifier follows a side as well: its middle, with the outline drawn to line up against.
  const looking: Point | null = active ?? (grab && "side" in grab
    ? [(corners[grab.side][0] + corners[(grab.side + 1) % 4][0]) / 2, (corners[grab.side][1] + corners[(grab.side + 1) % 4][1]) / 2]
    : null);
  const loupeLeft = looking && looking[0] > 0.5;
  /** The lines a locked drag runs on, as whole sides of the page. */
  const rails: number[] = !grab?.locked ? [] : "side" in grab
    ? [(grab.side + 3) % 4, (grab.side + 1) % 4]
    : grab.along !== null ? [grab.along] : [grab.corner, (grab.corner + 3) % 4];
  // Shown while dragging, so what letting go will do is never a surprise.
  const ghost = grab && "side" in grab && !same(corners, before.current) ? magnet(corners, grab.side, grab.locked) : null;
  const ghostLine = ghost && grab && "side" in grab ? [ghost[grab.side], ghost[(grab.side + 1) % 4]] : null;
  const lockNote = !grab?.locked ? null : "side" in grab
    ? `The ${SIDES[grab.side]} side slides, all angles kept`
    : grab.along !== null ? `Sliding along the ${SIDES[grab.along]} side` : "Move along either side";
  const preview = natural && valid ? previewMatrix(corners, natural, PREVIEW_LONG) : null;

  return (
    <Primitive.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Primitive.Portal>
        <Primitive.Content aria-describedby={undefined} className="fixed inset-0 z-[65] flex flex-col bg-background outline-none">
          <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 pt-[max(.5rem,env(safe-area-inset-top))]">
            <Primitive.Close asChild><Button variant="ghost" size="icon" aria-label="Close without changing"><X /></Button></Primitive.Close>
            <div className="min-w-0 flex-1 basis-40">
              <Primitive.Title className="text-sm font-semibold">Adjust the corners</Primitive.Title>
              <p className="truncate text-xs text-muted-foreground max-sm:hidden">Drag a corner or a side, holding Shift to keep the angles. Scroll or pinch to zoom, drag the photo to move it.</p>
            </div>
            <Button variant="secondary" size="sm" title="Forget this crop and let Scanloom find the page again" onClick={() => onSave({ redetect: true })}><ScanSearch /> Find again</Button>
            <Button variant="secondary" size="sm" title="The whole photo, no crop" onClick={() => edit(WHOLE)}><Maximize /> Whole photo</Button>
            <Button variant="secondary" size="sm" title="Back to where the corners were when you opened this" disabled={!changed} onClick={() => edit(initial)}><RotateCcw /> Reset</Button>
            <Button variant="secondary" size="icon-sm" aria-label="Undo" title="Undo the last change (Ctrl+Z)" disabled={!past.length} onClick={undo}><Undo2 /></Button>
            <Button variant="secondary" size="icon-sm" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!future.length} onClick={redo}><Redo2 /></Button>
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
                       onPointerMove={move} onPointerUp={drop} onPointerCancel={() => { if (grab) put(before.current); setGrab(null); }}>
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
                    {guides && active && !grab?.locked && (
                      <>
                        <line x1={active[0] * width} y1={-4000} x2={active[0] * width} y2={height + 4000} stroke="#fff" strokeOpacity=".7" strokeWidth="1" />
                        <line x1={-4000} y1={active[1] * height} x2={width + 4000} y2={active[1] * height} stroke="#fff" strokeOpacity=".7" strokeWidth="1" />
                      </>
                    )}
                    {rails.map((side) => {
                      const [x, y] = corners[side], [nx, ny] = corners[(side + 1) % 4];
                      const far = 8; // past the photo at any shape: a rail to the end of the screen
                      return <line key={`rail-${side}`} x1={(x - (nx - x) * far) * width} y1={(y - (ny - y) * far) * height} x2={(nx + (nx - x) * far) * width} y2={(ny + (ny - y) * far) * height}
                                   stroke="#fbbf24" strokeWidth="1.5" />;
                    })}
                    <polygon points={corners.map(([x, y]) => `${x * width},${y * height}`).join(" ")} fill="none" stroke={valid ? "#5eead4" : "#f87171"} strokeWidth="2" />
                    {ghostLine && <line x1={ghostLine[0][0] * width} y1={ghostLine[0][1] * height} x2={ghostLine[1][0] * width} y2={ghostLine[1][1] * height} stroke="#fff" strokeWidth="3" strokeDasharray="10 5" />}
                    {corners.map(([x, y], index) => {
                      const [nx, ny] = corners[(index + 1) % 4];
                      const mx = ((x + nx) / 2) * width, my = ((y + ny) / 2) * height;
                      const flat = Math.abs(nx - x) * width > Math.abs(ny - y) * height;
                      return (
                        <g key={SIDES[index]} transform={`translate(${mx} ${my})`} className="pointer-events-auto cursor-move" aria-label={`The ${SIDES[index]} side`}
                           onPointerDown={(event) => take(event, { side: index })}>
                          <circle r="20" fill="transparent" />
                          <rect x={flat ? -12 : -3} y={flat ? -3 : -12} width={flat ? 24 : 6} height={flat ? 6 : 24} rx="3" fill="#5eead4" stroke="#042f2e" strokeWidth="1" />
                        </g>
                      );
                    })}
                    {corners.map(([x, y], index) => (
                      <g key={NAMES[index]} transform={`translate(${x * width} ${y * height})`} role="slider" tabIndex={0} aria-label={`The ${NAMES[index]} corner`}
                         aria-valuetext={`${Math.round(x * 100)}% across, ${Math.round(y * 100)}% down`} aria-valuenow={Math.round(x * 100)}
                         className="pointer-events-auto cursor-grab outline-none active:cursor-grabbing [&:focus-visible>.ring]:opacity-100" onKeyDown={nudge(index)}
                         onPointerDown={(event) => take(event, { corner: index, along: null })}>
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
                <Button variant="ghost" size="sm" aria-label="Guides" aria-pressed={guides} title="Straight lines to judge the crop by" className={cn(guides && "bg-accent text-foreground")} onClick={() => setGuides(!guides)}><Grid3x3 /> <span className="max-sm:hidden">Guides</span></Button>
                <Button variant="ghost" size="sm" aria-label="Snap" aria-pressed={snap} title="A dropped corner goes to a real corner close by, a dropped side to the page's edge" className={cn(snap && "bg-accent text-foreground")} onClick={() => setSnap(!snap)}><Magnet /> <span className="max-sm:hidden">Snap</span></Button>
                <Button variant="ghost" size="sm" aria-label="Lock angle" aria-pressed={lockAngle} title="Keep the sides from turning while you drag. On a keyboard, hold Shift instead" className={cn(lockAngle && "bg-amber-400 text-black hover:bg-amber-300 hover:text-black")} onClick={() => setLockAngle(!lockAngle)}><Lock /> <span className="max-sm:hidden">Lock angle</span></Button>
              </div>
              {(lockNote || ghostLine) && (
                <div role="status" className={cn("pointer-events-none absolute top-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 px-2.5 py-1 text-xs font-medium whitespace-nowrap text-black shadow-lg max-sm:top-auto max-sm:bottom-16", lockNote ? "bg-amber-400" : "bg-white")}>
                  {lockNote ? <Lock className="size-3" /> : <Magnet className="size-3" />} {lockNote ?? "Let go to lay it on the page's edge"}{lockNote && ghostLine ? " · snaps to the edge" : ""}
                </div>
              )}
              {looking && (
                <div aria-hidden className="pointer-events-none absolute top-3 overflow-hidden rounded-full border-2 border-white bg-black shadow-2xl" style={{ width: LOUPE, height: LOUPE, [loupeLeft ? "left" : "right"]: 12 }}>
                  <img src={source} alt="" className="absolute max-w-none" style={{ width: width * ZOOM, height: height * ZOOM, left: LOUPE / 2 - looking[0] * width * ZOOM, top: LOUPE / 2 - looking[1] * height * ZOOM }} />
                  <svg className="absolute inset-0 size-full">
                    <polygon fill="none" stroke={rails.length ? "#fbbf24" : "#5eead4"} strokeWidth="1"
                             points={corners.map(([x, y]) => `${LOUPE / 2 + (x - looking[0]) * width * ZOOM},${LOUPE / 2 + (y - looking[1]) * height * ZOOM}`).join(" ")} />
                    {ghostLine && <line stroke="#fff" strokeWidth="2" strokeDasharray="6 3" x1={LOUPE / 2 + (ghostLine[0][0] - looking[0]) * width * ZOOM} y1={LOUPE / 2 + (ghostLine[0][1] - looking[1]) * height * ZOOM} x2={LOUPE / 2 + (ghostLine[1][0] - looking[0]) * width * ZOOM} y2={LOUPE / 2 + (ghostLine[1][1] - looking[1]) * height * ZOOM} />}
                  </svg>
                  {active && <span className="absolute top-1/2 left-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary" />}
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
