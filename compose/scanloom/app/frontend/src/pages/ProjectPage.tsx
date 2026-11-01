import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownUp, ArrowLeft, Camera, Check, Clock, Download, FileArchive, FileText, HardDrive, Infinity as Forever, Layers, LayoutGrid, List, MoreVertical, Palette, Plus, RotateCw, Timer, Trash2, TriangleAlert, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Failed } from "@/components/layout/States";
import { CropEditor } from "@/components/scan/CropEditor";
import { ExportDialog } from "@/components/scan/ExportDialog";
import { LookDialog } from "@/components/scan/LookDialog";
import { PageRow, PageTile, type PageActions } from "@/components/scan/PageViews";
import { PageViewer } from "@/components/scan/PageViewer";
import { ALL, Pagination } from "@/components/scan/Pagination";
import { Pipeline } from "@/components/scan/Pipeline";
import { Review } from "@/components/scan/Review";
import { ScanTitle } from "@/components/scan/ScanTitle";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Menu, MenuContent, MenuItem, MenuLink, MenuTrigger } from "@/components/ui/menu";
import { api, errorText, type Page, type PageEdit, type Point } from "@/lib/api";
import { addUploads, ACCEPT, useUploads } from "@/lib/uploads";
import { cn, formatBytes, plural, timeAgo, timeUntil } from "@/lib/utils";

const Scanner = lazy(() => import("@/scanner/Scanner").then((module) => ({ default: module.Scanner })));
const SIZES = [24, 48, 96];

function Skeleton() {
  return (
    <div aria-label="Loading" role="status">
      <div className="mb-3 h-8 w-64 animate-pulse rounded-lg bg-card" />
      <div className="mb-8 h-4 w-40 animate-pulse rounded bg-card" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {Array.from({ length: 10 }, (_, index) => <div key={index} className="aspect-[3/4] animate-pulse rounded-xl bg-card" />)}
      </div>
    </div>
  );
}

export function ProjectPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const size = Number(search.get("size")) || (Number(localStorage.getItem("scanloom.size")) || 24);
  const offset = Number(search.get("from")) || 0;
  const only = search.get("only") ?? undefined;
  const view = (search.get("view") ?? localStorage.getItem("scanloom.view") ?? (innerWidth < 640 ? "list" : "grid")) as "grid" | "list";
  const openId = search.get("page");

  const setParams = useCallback((changes: Record<string, string | null>, replace = false) => {
    setSearch((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      return next;
    }, { replace });
  }, [setSearch]);

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id), refetchInterval: (query) => (query.state.data?.status === "processing" ? 2500 : false) });
  // Also read every few seconds while working, in case a network in between holds the live stream back.
  const pages = useQuery({ queryKey: ["pages", id, size, offset, only], queryFn: () => api.pages(id, size, offset, only), placeholderData: keepPreviousData, refetchInterval: () => (project.data?.status === "processing" ? 2500 : false) });
  // All of them, only when something needs them: the viewer's next and previous.
  const allPages = useQuery({ queryKey: ["pages", id, "all"], queryFn: () => api.pages(id, ALL, 0), enabled: Boolean(openId), placeholderData: keepPreviousData });
  const uploads = useUploads(id);

  const [look, setLook] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [crop, setCrop] = useState<Page | null>(null);
  const [exporting, setExporting] = useState<"pdf" | "zip" | null>(null);
  const [removing, setRemoving] = useState<{ ids: string[]; label: string } | null>(null);
  const [deletingScan, setDeletingScan] = useState(false);
  const [camera, setCamera] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["project", id] });
    void client.invalidateQueries({ queryKey: ["pages", id] });
  }, [client, id]);
  const failure = (error: unknown) => toast.error(errorText(error));

  const edit = useMutation({ mutationFn: ({ ids, body }: { ids: string[]; body: PageEdit }) => api.editPages(id, ids, body), onSuccess: refresh, onError: failure });
  const crop_ = useMutation({
    mutationFn: ({ page, body }: { page: Page; body: PageEdit }) => api.editPage(id, page.id, body, page.rev),
    onSuccess: refresh,
    onError: (error) => { failure(error); refresh(); },
  });
  const remove = useMutation({
    mutationFn: (ids: string[]) => api.deletePages(id, ids),
    onSuccess: (_answer, ids) => { refresh(); setSelected([]); toast.success(`${plural(ids.length, "page")} deleted`); },
    onError: failure,
  });
  const retry = useMutation({ mutationFn: (ids?: string[]) => api.retryPages(id, ids), onSuccess: refresh, onError: failure });
  const removeScan = useMutation({
    mutationFn: () => api.deleteProject(id),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["projects"] }); navigate("/scans"); toast.success("Scan deleted"); },
    onError: failure,
  });

  const actions: PageActions = useMemo(() => ({
    open: (page) => setParams({ page: page.id }),
    rotate: (page) => edit.mutate({ ids: [page.id], body: { rotate_by: 90 } }),
    crop: setCrop,
    remove: (page) => setRemoving({ ids: [page.id], label: `page ${page.position + 1}` }),
  }), [edit, setParams]);

  const take = useCallback((files: File[], hint?: Point[]) => addUploads(id, files, hint), [id]);

  useEffect(() => {
    const hasFiles = (event: DragEvent) => event.dataTransfer?.types.includes("Files");
    const enter = (event: DragEvent) => { if (hasFiles(event)) { event.preventDefault(); setOver(true); } };
    const leave = (event: DragEvent) => { if (!event.relatedTarget) setOver(false); };
    const drop = (event: DragEvent) => { if (hasFiles(event)) { event.preventDefault(); setOver(false); take(Array.from(event.dataTransfer!.files)); } };
    window.addEventListener("dragover", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [take]);

  if (project.isPending) return <Skeleton />;
  if (project.error) {
    return (
      <div className="flex flex-col items-start gap-4">
        <Failed message={errorText(project.error, "This scan couldn't be loaded.")} />
        <Button asChild variant="secondary"><Link to="/scans"><ArrowLeft /> All scans</Link></Button>
      </div>
    );
  }
  const data = project.data;
  const list = pages.data;
  const counts = list?.counts;
  const items = list?.items ?? [];
  const canDownload = data.completed_count > 0;
  const toggle = (pageId: string) => setSelected((current) => (current.includes(pageId) ? current.filter((value) => value !== pageId) : [...current, pageId]));
  const viewerPages = allPages.data?.items ?? items;
  const viewerAt = openId ? viewerPages.findIndex((page) => page.id === openId) : -1;

  return (
    <div className={cn("pb-16", over && "outline-2 outline-offset-8 outline-primary outline-dashed rounded-xl")}>
      <Link to="/scans" className="mb-2 inline-flex min-h-8 items-center gap-1.5 rounded text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring"><ArrowLeft className="size-4" /> All scans</Link>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1 basis-72">
          <ScanTitle id={id} title={data.title} rev={data.rev} />
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground [&_svg]:size-3.5">
            <span className="flex items-center gap-1.5"><Layers /> {plural(data.page_count, "page")}</span>
            {data.bytes > 0 && <span className="flex items-center gap-1.5"><HardDrive /> {formatBytes(data.bytes)}</span>}
            <span className="flex items-center gap-1.5"><Clock /> {timeAgo(data.created_at)}</span>
            {data.expires_at ? <span className="flex items-center gap-1.5"><Timer /> Deleted in {timeUntil(data.expires_at)}</span>
              : <span className="flex items-center gap-1.5" title="This scan has no expiry: it stays until you delete it"><Forever /> Kept until you delete it</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={!canDownload} title="Transform all the pages: color, clean paper or black & white" onClick={() => setLook(true)}><Palette /> Change the look</Button>
          <Button disabled={!canDownload} onClick={() => setExporting("pdf")}><FileText /> PDF</Button>
          <Button variant="secondary" disabled={!canDownload} onClick={() => setExporting("zip")}><FileArchive /> ZIP</Button>
          <Menu>
            <MenuTrigger asChild><Button variant="secondary" size="icon" className="size-10" aria-label="More for this scan"><MoreVertical /></Button></MenuTrigger>
            <MenuContent>
              {data.exports.filter((item) => item.download_url).slice(0, 4).map((item) => (
                <MenuLink key={item.id} href={item.download_url!} download><Download /> {item.kind.toUpperCase()} of {plural(item.page_count, "page")}{item.bytes ? ` · ${formatBytes(item.bytes)}` : ""}</MenuLink>
              ))}
              <MenuItem danger onSelect={() => setDeletingScan(true)}><Trash2 /> Delete this scan</MenuItem>
            </MenuContent>
          </Menu>
        </div>
      </header>

      <Pipeline project={data} measuredAt={project.dataUpdatedAt} />
      {data.held_count > 0 && <Review project={data} onAdd={() => input.current?.click()} />}

      {data.failed_count > 0 && (
        <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-3 text-sm">
          <p className="flex min-w-0 items-start gap-2 text-destructive"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> <span>{plural(data.failed_count, "page")} couldn't be made{data.error ? `: ${data.error}` : "."} The others are fine.</span></p>
          <Button variant="secondary" size="sm" onClick={() => retry.mutate(undefined)}><RotateCw /> Try again</Button>
        </div>
      )}

      {/* The bar over the pages: what to show, and how; or, while selecting, what to do with those selected. */}
      {data.held_count > 0 && !items.length && <input ref={input} hidden type="file" multiple accept={ACCEPT} onChange={(event) => { take(Array.from(event.target.files ?? [])); event.target.value = ""; }} />}
      <div hidden={data.held_count > 0 && !items.length} className="sticky top-14 z-20 -mx-1 mb-4 flex flex-wrap items-center gap-2 rounded-xl bg-background/90 px-1 py-2 backdrop-blur lg:top-0">
        {selected.length > 0 ? (
          <>
            <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums" aria-live="polite"><Check className="size-4 text-primary" /> {plural(selected.length, "page")} selected</span>
            <Button variant="ghost" size="sm" onClick={() => setSelected(items.length && items.every((page) => selected.includes(page.id)) ? selected.filter((value) => !items.some((page) => page.id === value)) : [...new Set([...selected, ...items.map((page) => page.id)])])}>
              <Check /> {items.every((page) => selected.includes(page.id)) ? "None on this page" : "All on this page"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected([])}><X /> Clear</Button>
            <div className="ml-auto flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={() => edit.mutate({ ids: selected, body: { rotate_by: 90 } })}><RotateCw /> Rotate</Button>
              <Button variant="secondary" size="sm" onClick={() => setExporting("pdf")}><Download /> Download</Button>
              <Button variant="destructive" size="sm" onClick={() => setRemoving({ ids: selected, label: plural(selected.length, "page") })}><Trash2 /> Delete</Button>
            </div>
          </>
        ) : (
          <>
            <div role="tablist" aria-label="Which pages" className="flex rounded-lg border border-border bg-card p-0.5 text-sm">
              {([[undefined, `All ${counts?.total ?? data.page_count}`, Layers], ["review", `To review ${counts?.review ?? data.warning_count}`, TriangleAlert], ["failed", `Failed ${counts?.failed ?? data.failed_count}`, X]] as const)
                .filter(([key]) => !key || (key === "review" ? (counts?.review ?? data.warning_count) : (counts?.failed ?? data.failed_count)))
                .map(([key, label, Icon]) => (
                  <button key={label} type="button" role="tab" aria-selected={only === key}
                          className={cn("flex min-h-8 items-center gap-1.5 rounded-md px-2.5 outline-none [&_svg]:size-3.5 transition-colors focus-visible:ring-[3px] focus-visible:ring-ring", only === key ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}
                          onClick={() => setParams({ only: key ?? null, from: null })}><Icon /> {label}</button>
                ))}
            </div>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => input.current?.click()}><Plus /> Add photos</Button>
              {!(data.held_count > 0 && !items.length) && <input ref={input} hidden type="file" multiple accept={ACCEPT} onChange={(event) => { take(Array.from(event.target.files ?? [])); event.target.value = ""; }} />}
              <Button variant="secondary" size="sm" className="sm:hidden" onClick={() => setCamera(true)} aria-label="Add with the camera"><Camera /> Camera</Button>
              {data.page_count > 1 && !data.held_count && <Button asChild variant="secondary" size="sm"><Link to={`/scans/${id}/arrange`}><ArrowDownUp /> Arrange</Link></Button>}
              <div role="radiogroup" aria-label="Layout" className="flex rounded-lg border border-border bg-card p-0.5">
                {([["grid", LayoutGrid, "Grid"], ["list", List, "List"]] as const).map(([key, Icon, label]) => (
                  <button key={key} type="button" role="radio" aria-checked={view === key} aria-label={label} title={label}
                          onClick={() => { localStorage.setItem("scanloom.view", key); setParams({ view: key }, true); }}
                          className={cn("flex size-7 items-center justify-center rounded-md outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring [&_svg]:size-4", view === key ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}><Icon /></button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {data.held_count > 0 && !items.length ? null : pages.error ? <Failed message={errorText(pages.error, "The pages couldn't be loaded.")} /> : !list ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" role="status" aria-label="Loading">{Array.from({ length: 10 }, (_, index) => <div key={index} className="aspect-[3/4] animate-pulse rounded-xl bg-card" />)}</div>
      ) : !items.length && !uploads.length ? (
        <button type="button" onClick={() => input.current?.click()} className="w-full rounded-xl border-2 border-dashed border-foreground/15 p-10 text-center text-sm text-muted-foreground hover:border-foreground/30 hover:text-foreground">
          {only ? "No page here." : "No pages yet: drop photos here, or choose some."}
        </button>
      ) : view === "grid" ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((page) => <PageTile key={page.id} page={page} number={page.position + 1} actions={actions} selected={selected.includes(page.id)} onSelect={() => toggle(page.id)} />)}
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {items.map((page) => <PageRow key={page.id} page={page} number={page.position + 1} actions={actions} selected={selected.includes(page.id)} onSelect={() => toggle(page.id)} />)}
        </ul>
      )}

      {list && list.total > 0 && (
        <div>
          <Pagination total={list.total} size={size} sizes={SIZES} offset={offset} noun="pages" onChange={(next) => {
            localStorage.setItem("scanloom.size", String(next.size));
            setParams({ size: String(next.size), from: next.offset ? String(next.offset) : null });
            window.scrollTo({ top: 0 });
          }} />
        </div>
      )}

      {openId && viewerAt >= 0 && (
        <PageViewer pages={viewerPages} at={viewerAt}
                    onGo={(index) => setParams({ page: viewerPages[index].id }, true)} onClose={() => setParams({ page: null })}
                    onEdit={(page, body) => edit.mutate({ ids: [page.id], body })} onCrop={setCrop} onRemove={actions.remove} onRetry={(page) => retry.mutate([page.id])} />
      )}
      {crop && <CropEditor page={crop} onClose={() => setCrop(null)} onSave={(body) => { crop_.mutate({ page: crop, body }); setCrop(null); }} />}
      {look && <LookDialog project={data} sample={items.find((page) => page.thumb_url)?.thumb_url ?? null} onClose={() => setLook(false)} onChanged={refresh} />}
      {exporting && <ExportDialog project={data} initial={exporting} selected={selected} onClose={() => setExporting(null)} />}
      {camera && (
        <Suspense fallback={<div className="fixed inset-0 z-[70] bg-black" />}>
          <Scanner onCapture={(file, hint) => take([file], hint)} onDone={() => setCamera(false)} />
        </Suspense>
      )}
      <Confirm open={Boolean(removing)} title={`Delete ${removing?.label}?`} text="The page and its files are erased. This can't be undone." action="Delete"
               onCancel={() => setRemoving(null)}
               onConfirm={() => {
                 const ids = removing!.ids;
                 setRemoving(null);
                 if (openId && ids.includes(openId)) setParams({ page: null });
                 remove.mutate(ids);
               }} />
      <Confirm open={deletingScan} title={`Delete ${data.title}?`} text={`Its ${plural(data.page_count, "page")} and files are erased. This can't be undone.`} action="Delete"
               onCancel={() => setDeletingScan(false)} onConfirm={() => { setDeletingScan(false); removeScan.mutate(); }} />
    </div>
  );
}
