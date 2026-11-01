import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileArchive, FileText, History, LayoutGrid, List, ListFilter, Loader2, Search, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed } from "@/components/layout/States";
import { Pagination } from "@/components/scan/Pagination";
import { ScanCard } from "@/components/scan/ScanCard";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText } from "@/lib/api";
import { cn, formatBytes, plural } from "@/lib/utils";

function Skeleton() {
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" aria-hidden>{Array.from({ length: 10 }, (_, index) => <div key={index} className="aspect-[4/4] animate-pulse rounded-xl bg-card" />)}</div>;
}

function BatchDownload({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [format, setFormat] = useState<"pdf" | "images">("pdf");
  const [job, setJob] = useState<{ id: string; project: string; url: string } | null>(null);
  const started = useRef(false);
  const make = useMutation({
    mutationFn: () => api.batchExport({ project_ids: ids, format, quality: "balanced" }),
    onSuccess: (answer) => setJob({ id: answer.id, project: answer.project, url: answer.download_url }),
  });
  const holder = useQuery({
    queryKey: ["project", job?.project], queryFn: () => api.project(job!.project), enabled: Boolean(job),
    refetchInterval: (query) => { const item = query.state.data?.exports.find((value) => value.id === job?.id); return item?.status === "ready" || item?.status === "failed" ? false : 1500; },
  });
  const current = holder.data?.exports.find((item) => item.id === job?.id);
  useEffect(() => {
    if (current?.status !== "ready" || !job || started.current) return;
    started.current = true;
    const link = document.createElement("a");
    link.href = job.url;
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
  }, [current?.status, job]);
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Download {plural(ids.length, "scan")}</DialogTitle><DialogDescription className="sr-only">One ZIP of the selected scans.</DialogDescription></DialogHeader>
        {!job ? (
          <>
            <DialogBody>
              <p className="mb-3 text-sm text-muted-foreground">One ZIP holding:</p>
              <div role="radiogroup" className="flex flex-col gap-2 sm:flex-row">
                {([["pdf", FileText, "A PDF per scan", "Named after each scan"], ["images", FileArchive, "A folder of images per scan", "JPEG, one per page"]] as const).map(([key, Icon, title, hint]) => (
                  <button key={key} type="button" role="radio" aria-checked={format === key} onClick={() => setFormat(key)}
                          className={cn("flex flex-1 items-start gap-2 rounded-lg border p-3 text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring", format === key ? "border-primary bg-primary/10" : "border-border hover:bg-accent")}>
                    <Icon className="mt-0.5 size-4 shrink-0" /><span><span className="block text-sm font-medium">{title}</span><span className="block text-xs text-muted-foreground">{hint}</span></span>
                  </button>
                ))}
              </div>
              {make.error && <p className="mt-3 flex items-start gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {errorText(make.error)}</p>}
            </DialogBody>
            <DialogFooter className="justify-end">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button disabled={make.isPending} onClick={() => make.mutate()}>{make.isPending ? <Loader2 className="animate-spin" /> : <Download />} Make the ZIP</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogBody className="flex flex-col items-center gap-3 py-8 text-center" aria-live="polite">
              {current?.status === "failed" ? <TriangleAlert className="size-8 text-destructive" /> : current?.status === "ready" ? <Check className="size-8 text-success" /> : <Loader2 className="size-8 animate-spin text-primary" />}
              <p className="font-medium">{current?.status === "failed" ? "It couldn't be made" : current?.status === "ready" ? "Your download has started" : "Making the ZIP…"}</p>
              <p className="text-sm text-muted-foreground">{current?.status === "failed" ? current.error : current?.status === "ready" ? `${current.filename}${current.bytes ? ` · ${formatBytes(current.bytes)}` : ""}` : "It downloads by itself when it is ready."}</p>
            </DialogBody>
            <DialogFooter className="justify-end">
              {current?.status === "ready" && <Button variant="secondary" asChild><a href={job.url} download><Download /> Download again</a></Button>}
              <Button onClick={onClose}>Close</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function ScansPage() {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [view, setView] = useState<"grid" | "list">(() => (localStorage.getItem("scanloom.scansView") as "grid" | "list") || "grid");
  const [window_, setWindow] = useState({ size: 24, offset: 0 });
  const [selected, setSelected] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    if (search.trim() === q) return;
    const timer = window.setTimeout(() => { setQ(search.trim()); setWindow((value) => ({ ...value, offset: 0 })); }, 250);
    return () => window.clearTimeout(timer);
  }, [search, q]);
  const params = { limit: window_.size, offset: window_.offset, q: q || undefined, status: status === "all" ? undefined : status };
  const scans = useQuery({
    queryKey: ["projects", params], queryFn: () => api.projects(params), placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some((scan) => scan.status === "processing") ? 3000 : false),
  });
  const total = scans.data?.total ?? 0;
  const items = scans.data?.items ?? [];
  const toggle = (id: string) => setSelected((list) => (list.includes(id) ? list.filter((value) => value !== id) : [...list, id]));
  const clear = () => setSelected([]);
  const everything = useMutation({
    mutationFn: () => api.projects({ limit: 1000, offset: 0, q: q || undefined, status: status === "all" ? undefined : status }),
    onSuccess: (answer) => setSelected([...new Set([...selected, ...answer.items.map((scan) => scan.public_id)])]),
    onError: (error) => toast.error(errorText(error)),
  });
  const allHere = items.length > 0 && items.every((scan) => selected.includes(scan.public_id));
  const remove = useMutation({
    mutationFn: async (ids: string[]) => { for (const id of ids) await api.deleteProject(id); },
    onSuccess: (_answer, ids) => { void client.invalidateQueries({ queryKey: ["projects"] }); toast.success(`${plural(ids.length, "scan")} deleted`); clear(); },
    onError: (error) => { void client.invalidateQueries({ queryKey: ["projects"] }); toast.error(errorText(error)); },
  });

  return (
    <div className="pb-16">
      <PageHeader title="Scans" description="They stay here until you delete them, on every device you sign in on." actions={<Button asChild><Link to="/">New scan</Link></Button>} />
      <div className="sticky top-14 z-20 mb-4 flex flex-wrap items-center gap-2 bg-background/95 py-2 backdrop-blur lg:top-0">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" aria-label="Search by name" placeholder="Search by name" className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <Select value={status} onValueChange={(value) => { setStatus(value); setWindow((current) => ({ ...current, offset: 0 })); }}>
          <SelectTrigger aria-label="Show" className="w-44"><ListFilter className="size-4" /><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All scans</SelectItem>
            <SelectItem value="draft">Not scanned yet</SelectItem>
            <SelectItem value="processing">In progress</SelectItem>
            <SelectItem value="ready">Finished</SelectItem>
            <SelectItem value="failed">With failed pages</SelectItem>
          </SelectContent>
        </Select>
        <div role="radiogroup" aria-label="Layout" className="flex rounded-lg border border-border bg-card p-0.5">
          {([["grid", LayoutGrid, "Grid"], ["list", List, "List"]] as const).map(([key, Icon, label]) => (
            <button key={key} type="button" role="radio" aria-checked={view === key} aria-label={label} title={label}
                    onClick={() => { setView(key); localStorage.setItem("scanloom.scansView", key); }}
                    className={cn("flex size-[2.125rem] items-center justify-center rounded-md outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring [&_svg]:size-4", view === key ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}><Icon /></button>
          ))}
        </div>
        {selected.length > 0 && (
          <div className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-primary/10 px-3 py-1.5" role="region" aria-label="Selected scans">
            <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums" aria-live="polite"><Check className="size-4 text-primary" /> {plural(selected.length, "scan")} selected</span>
            <Button variant="ghost" size="sm" onClick={() => setSelected(allHere ? selected.filter((id) => !items.some((scan) => scan.public_id === id)) : [...new Set([...selected, ...items.map((scan) => scan.public_id)])])}>{allHere ? "None on this page" : "All on this page"}</Button>
            {total > items.length && selected.length < total && <Button variant="ghost" size="sm" disabled={everything.isPending} onClick={() => everything.mutate()}>All {total}</Button>}
            <Button variant="ghost" size="sm" onClick={clear}><X /> Clear</Button>
            <div className="ml-auto flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => setDownloading(true)}><Download /> Download</Button>
              <Button variant="destructive" size="sm" disabled={remove.isPending} onClick={() => setDeleting(true)}><Trash2 /> Delete</Button>
            </div>
          </div>
        )}
      </div>
      {scans.isPending ? <Skeleton /> : scans.error ? <Failed message={errorText(scans.error, "The scans couldn't be loaded.")} /> : !items.length ? (
        <Empty icon={History} title={q || status !== "all" ? "No scan matches" : "No scans yet"}>
          {q || status !== "all" ? "Try another name or filter." : "Your scans will be here."}
        </Empty>
      ) : view === "grid" ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((scan) => <ScanCard key={scan.public_id} scan={scan} selected={selected.includes(scan.public_id)} onSelect={() => toggle(scan.public_id)} />)}
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {items.map((scan) => <ScanCard key={scan.public_id} row scan={scan} selected={selected.includes(scan.public_id)} onSelect={() => toggle(scan.public_id)} />)}
        </ul>
      )}
      <Pagination total={total} size={window_.size} sizes={[24, 48, 96]} offset={window_.offset} noun="scans" onChange={(next) => { setWindow(next); window.scrollTo({ top: 0 }); }} />
      {downloading && <BatchDownload ids={selected} onClose={() => setDownloading(false)} />}
      <Confirm open={deleting} title={`Delete ${plural(selected.length, "scan")}?`} text="Their pages and files are erased. This can't be undone." action="Delete"
               onCancel={() => setDeleting(false)} onConfirm={() => { setDeleting(false); remove.mutate(selected); }} />
    </div>
  );
}
