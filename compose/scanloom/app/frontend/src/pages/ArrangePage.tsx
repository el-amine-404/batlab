import { closestCenter, DndContext, type DragEndEvent, DragOverlay, type DragStartEvent, KeyboardSensor, MouseSensor, TouchSensor, useSensor, useSensors } from "@dnd-kit/core";
import { arrayMove, rectSortingStrategy, SortableContext, sortableKeyboardCoordinates, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownAZ, ArrowLeft, ArrowLeftRight, Check, ChevronsDown, ChevronsUp, CloudCheck, Hash, Loader2, Redo2, Undo2, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { Failed, Loading } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errorText, type Page } from "@/lib/api";
import { cn, naturalCompare, plural } from "@/lib/utils";

const SIZES = { S: "7rem", M: "10rem", L: "14rem" } as const;
type Size = keyof typeof SIZES;

function Tile({ page, number, selected, dimmed, overlay, onToggle }: { page: Page; number: number; selected: boolean; dimmed?: boolean; overlay?: boolean; onToggle?: (event: React.MouseEvent) => void }) {
  return (
    <div onClick={onToggle} className={cn("relative aspect-[3/4] w-full overflow-hidden rounded-lg border-2 bg-tile p-1 transition-[border-color,opacity]",
                                          selected ? "border-primary" : "border-border hover:border-foreground/40", dimmed && "opacity-30", overlay && "shadow-2xl ring-2 ring-primary")}>
      {page.thumb_url ? <img src={page.thumb_url} alt="" loading="lazy" decoding="async" draggable={false} className="size-full object-contain" /> : <span className="flex size-full items-center justify-center text-xs text-muted-foreground">No picture</span>}
      <span className="absolute bottom-1 left-1 min-w-6 rounded-md bg-black/80 px-1.5 py-0.5 text-center text-xs font-semibold text-white tabular-nums">{number}</span>
      {selected && <span className="absolute top-1 right-1 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground"><Check className="size-3.5" /></span>}
    </div>
  );
}

function Sortable({ page, number, selected, dimmed, onToggle }: { page: Page; number: number; selected: boolean; dimmed: boolean; onToggle: (event: React.MouseEvent) => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: page.id });
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, touchAction: "manipulation" }}
         className={cn("cursor-grab select-none outline-none active:cursor-grabbing focus-visible:rounded-lg focus-visible:ring-[3px] focus-visible:ring-ring", isDragging && "opacity-30")}
         {...attributes} {...listeners} aria-label={`Page ${number}, ${page.original_name}${selected ? ", selected" : ""}`}
         onContextMenu={(event) => event.preventDefault()}>
      <Tile page={page} number={number} selected={selected} dimmed={dimmed} onToggle={onToggle} />
    </div>
  );
}

/**
 * Arranging is a screen of its own: every page, whatever the length of the scan.
 * Drag one, or several selected: on a phone, press a moment first, so that
 * scrolling stays scrolling. Each arrangement is saved by itself and can be undone.
 */
export function ArrangePage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const pages = useQuery({ queryKey: ["pages", id, "all"], queryFn: () => api.pages(id, 1000, 0), refetchOnWindowFocus: false, staleTime: Infinity });
  const byId = useMemo(() => new Map((pages.data?.items ?? []).map((page) => [page.id, page])), [pages.data]);

  const [order, setOrder] = useState<string[] | null>(null);
  const [past, setPast] = useState<string[][]>([]);
  const [future, setFuture] = useState<string[][]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [size, setSize] = useState<Size>(() => (localStorage.getItem("scanloom.tile") as Size) || (innerWidth < 640 ? "S" : "M"));
  const [saving, setSaving] = useState<"idle" | "waiting" | "saved">("idle");
  const [position, setPosition] = useState("");
  const timer = useRef<number>(undefined);
  const latest = useRef<string[] | null>(null);
  const rev = useRef<number | undefined>(undefined);
  if (rev.current === undefined && project.data) rev.current = project.data.rev;

  useEffect(() => {
    if (pages.data && !order) setOrder(pages.data.items.map((page) => page.id));
  }, [pages.data, order]);

  const save = useCallback(async () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    if (!latest.current) return;
    const sent = latest.current;
    latest.current = null;
    try {
      rev.current = (await api.editProject(id, { page_order: sent }, rev.current)).rev;
      setSaving(latest.current ? "waiting" : "saved");
      void client.invalidateQueries({ queryKey: ["pages", id], predicate: (query) => query.queryKey[2] !== "all" });
    } catch (error) {
      toast.error(errorText(error));
      setSaving("idle");
      setOrder(null);
      setPast([]);
      setFuture([]);
      rev.current = undefined;
      void project.refetch();
      void pages.refetch();
    }
  }, [client, id, pages, project]);

  // What was still to be saved goes when leaving too.
  useEffect(() => () => {
    if (latest.current) void api.editProject(id, { page_order: latest.current }, rev.current).then(() => client.invalidateQueries({ queryKey: ["pages", id] }));
    else void client.invalidateQueries({ queryKey: ["pages", id] });
  }, [client, id]);

  const apply = useCallback((next: string[]) => {
    setOrder((current) => {
      if (current) setPast((list) => [...list.slice(-49), current]);
      return next;
    });
    setFuture([]);
    latest.current = next;
    setSaving("waiting");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), 450);
  }, [save]);

  const travel = useCallback((from: string[][], setFrom: (list: string[][]) => void, setTo: (update: (list: string[][]) => string[][]) => void) => {
    if (!from.length || !order) return;
    const target = from[from.length - 1];
    setFrom(from.slice(0, -1));
    setTo((list) => [...list, order]);
    setOrder(target);
    latest.current = target;
    setSaving("waiting");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), 450);
  }, [order, save]);
  const undo = useCallback(() => travel(past, setPast, setFuture), [past, travel]);
  const redo = useCallback(() => travel(future, setFuture, setPast), [future, travel]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  if (project.error || pages.error) return <Failed message={errorText(project.error ?? pages.error, "This scan couldn't be loaded.")} />;
  if (!project.data || !order) return <Loading />;

  const moving = (lead: string) => (selected.has(lead) && selected.size > 1 ? order.filter((pageId) => selected.has(pageId)) : [lead]);

  const dragStart = (event: DragStartEvent) => {
    setActive(String(event.active.id));
    navigator.vibrate?.(15);
  };
  const dragEnd = (event: DragEndEvent) => {
    setActive(null);
    const { active: from, over } = event;
    if (!over || from.id === over.id) return;
    const lead = String(from.id), target = String(over.id);
    const group = moving(lead);
    if (group.length === 1) return apply(arrayMove(order, order.indexOf(lead), order.indexOf(target)));
    if (group.includes(target)) return;
    const rest = order.filter((pageId) => !group.includes(pageId));
    const forward = order.indexOf(lead) < order.indexOf(target);
    rest.splice(rest.indexOf(target) + (forward ? 1 : 0), 0, ...group);
    apply(rest);
  };

  const toggle = (pageId: string) => (event: React.MouseEvent) => {
    const next = new Set(selected);
    if (event.shiftKey && anchor) {
      const [a, b] = [order.indexOf(anchor), order.indexOf(pageId)].sort((x, y) => x - y);
      order.slice(a, b + 1).forEach((value) => next.add(value));
    } else if (next.has(pageId)) next.delete(pageId);
    else next.add(pageId);
    setAnchor(pageId);
    setSelected(next);
  };

  const chosen = order.filter((pageId) => selected.has(pageId));
  const rest = order.filter((pageId) => !selected.has(pageId));
  const moveTo = (place: number) => {
    const at = Math.max(0, Math.min(rest.length, place));
    apply([...rest.slice(0, at), ...chosen, ...rest.slice(at)]);
  };
  const submitPosition = (event: FormEvent) => {
    event.preventDefault();
    const place = Number(position);
    if (!chosen.length || !Number.isInteger(place) || place < 1) return;
    moveTo(place - 1);
    setPosition("");
  };
  const lead = active ? byId.get(active) : null;
  const group = active ? moving(active) : [];

  return (
    <div>
      <div className="sticky top-14 z-20 -mx-1 mb-4 flex flex-col gap-2 rounded-xl bg-background/95 px-1 py-2 backdrop-blur lg:top-0">
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="ghost" size="sm"><Link to={`/scans/${id}`}><ArrowLeft /> Done</Link></Button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">Arrange {project.data.title} <span className="font-normal text-muted-foreground">· {plural(order.length, "page")}</span></h1>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            {saving === "waiting" ? <><Loader2 className="size-3.5 animate-spin" /> Saving</> : saving === "saved" ? <><CloudCheck className="size-3.5 text-success" /> Saved</> : null}
          </span>
          <Button variant="secondary" size="icon-sm" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!past.length} onClick={undo}><Undo2 /></Button>
          <Button variant="secondary" size="icon-sm" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!future.length} onClick={redo}><Redo2 /></Button>
          <div role="radiogroup" aria-label="Size of the pages" className="flex rounded-lg border border-border bg-card p-0.5">
            {(Object.keys(SIZES) as Size[]).map((key) => (
              <button key={key} type="button" role="radio" aria-checked={size === key} onClick={() => { setSize(key); localStorage.setItem("scanloom.tile", key); }}
                      className={cn("size-7 rounded-md text-xs font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring", size === key ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>{key}</button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {chosen.length ? (
            <>
              <span className="text-sm font-medium tabular-nums" aria-live="polite">{chosen.length} selected</span>
              <Button variant="secondary" size="sm" onClick={() => moveTo(0)}><ChevronsUp /> To the start</Button>
              <Button variant="secondary" size="sm" onClick={() => moveTo(rest.length)}><ChevronsDown /> To the end</Button>
              <form onSubmit={submitPosition} className="flex items-center gap-1">
                <Input aria-label="Move to page number" className="h-8 w-20 text-sm" type="number" inputMode="numeric" min={1} max={order.length} placeholder="Page" value={position} onChange={(event) => setPosition(event.target.value)} />
                <Button type="submit" variant="secondary" size="sm" disabled={!position}><Hash /> Move</Button>
              </form>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X /> Clear</Button>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground max-sm:hidden">Drag a page to move it. Click to select several, Shift-click for a stretch. Keyboard: Tab to a page, Space, arrow keys, Space.</p>
              <p className="text-sm text-muted-foreground sm:hidden">Press and hold a page, then drag. Tap to select several.</p>
              <div className="ml-auto flex gap-2">
                <Button variant="secondary" size="sm" onClick={() => setSelected(new Set(order))}><Check /> All</Button>
                <Button variant="secondary" size="sm" onClick={() => apply([...order].reverse())}><ArrowLeftRight /> Reverse</Button>
                <Button variant="secondary" size="sm" onClick={() => apply([...order].sort((a, b) => naturalCompare(byId.get(a)?.original_name ?? "", byId.get(b)?.original_name ?? "")))}><ArrowDownAZ /> By file name</Button>
              </div>
            </>
          )}
        </div>
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={dragStart} onDragEnd={dragEnd} onDragCancel={() => setActive(null)} autoScroll={{ threshold: { x: 0, y: 0.18 }, acceleration: 14 }}>
        <SortableContext items={order} strategy={rectSortingStrategy}>
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${SIZES[size]}, 1fr))` }}>
            {order.map((pageId, index) => {
              const page = byId.get(pageId);
              return page ? <Sortable key={pageId} page={page} number={index + 1} selected={selected.has(pageId)} dimmed={Boolean(active) && group.includes(pageId) && pageId !== active} onToggle={toggle(pageId)} /> : null;
            })}
          </div>
        </SortableContext>
        <DragOverlay>
          {lead && (
            <div className="relative cursor-grabbing">
              <Tile page={lead} number={order.indexOf(lead.id) + 1} selected={false} overlay />
              {group.length > 1 && <span className="absolute -top-2 -right-2 flex size-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{group.length}</span>}
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
