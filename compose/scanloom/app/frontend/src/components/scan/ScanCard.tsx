import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, FileImage, Hourglass, Infinity as Forever, Layers, Loader2, MoreVertical, Pencil, ScanLine, Timer, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tick } from "@/components/ui/tick";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { api, errorText, type ProjectSummary } from "@/lib/api";
import { cn, eta, plural, timeAgo, timeUntil } from "@/lib/utils";

export function ScanCard({ scan, selected = false, onSelect, row = false }: { scan: ProjectSummary; selected?: boolean; onSelect?: () => void; row?: boolean }) {
  const client = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [draft, setDraft] = useState(scan.title);
  const refresh = () => void client.invalidateQueries({ queryKey: ["projects"] });
  const rename = useMutation({ mutationFn: (title: string) => api.editProject(scan.id, { title }), onSuccess: () => { refresh(); setRenaming(false); }, onError: (error) => toast.error(errorText(error)) });
  const remove = useMutation({ mutationFn: () => api.deleteProject(scan.id), onSuccess: () => { refresh(); toast.success(`${scan.title} deleted`); }, onError: (error) => toast.error(errorText(error)) });
  const active = scan.status === "processing";
  const percent = scan.page_count ? Math.round(((scan.completed_count + scan.failed_count) / scan.page_count) * 100) : 0;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.trim()) rename.mutate(draft.trim());
  };
  const waiting = scan.held_count > 0;
  const status = (
    <>
      <span className="flex items-center gap-1"><Layers className="size-3" /> {active && !waiting ? `${scan.completed_count + scan.failed_count} of ${plural(scan.page_count, "page")}` : plural(scan.page_count, "page")}</span>
      {waiting ? <span className="flex items-center gap-1 font-medium text-primary"><ScanLine className="size-3" /> Not scanned yet</span>
        : scan.eta_seconds ? <span className="flex items-center gap-1 font-medium text-foreground"><Hourglass className="size-3" /> {eta(scan.eta_seconds)} left</span>
        : <span className="flex items-center gap-1"><Clock className="size-3" /> {timeAgo(scan.created_at)}</span>}
      {scan.expires_at ? <span className="flex items-center gap-1 text-warning" title="Deleted automatically then"><Timer className="size-3" /> {timeUntil(scan.expires_at)} left</span>
        : <span className="flex items-center gap-1" title="No expiry: it stays until you delete it"><Forever className="size-3" /> Kept</span>}
      {scan.failed_count > 0 && <span className="flex items-center gap-1 text-destructive"><TriangleAlert className="size-3" /> {scan.failed_count} failed</span>}
      {!scan.failed_count && scan.warning_count > 0 && <span className="flex items-center gap-1 text-warning" title={`${plural(scan.warning_count, "page")} to review`}><TriangleAlert className="size-3" /> {scan.warning_count}</span>}
    </>
  );
  const name = <Link to={`/scans/${scan.id}`} className="outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:after:ring-[3px] focus-visible:after:ring-ring focus-visible:after:ring-inset">{scan.title}</Link>;
  const tick = onSelect && <Tick checked={selected} label={`Select ${scan.title}`} onToggle={onSelect} className={row ? "[&>span]:border-muted-foreground" : "absolute top-1 left-1"} />;
  const menu = (
    <Menu>
      <MenuTrigger asChild><Button variant="ghost" size="icon-sm" className="relative z-10 shrink-0" aria-label={`More for ${scan.title}`}><MoreVertical /></Button></MenuTrigger>
      <MenuContent>
        <MenuItem onSelect={() => { setDraft(scan.title); setRenaming(true); }}><Pencil /> Rename</MenuItem>
        <MenuItem danger onSelect={() => setDeleting(true)}><Trash2 /> Delete</MenuItem>
      </MenuContent>
    </Menu>
  );
  const picture = scan.thumbnail_url ? <img src={scan.thumbnail_url} alt="" loading="lazy" decoding="async" width="480" height="360" className="size-full object-contain" />
    : <span className="flex size-full items-center justify-center text-muted-foreground">{active ? <Loader2 className="size-5 animate-spin" /> : <FileImage className="size-5" />}</span>;
  const dialogs = (
    <>
      <Confirm open={deleting} title={`Delete ${scan.title}?`} text={`Its ${plural(scan.page_count, "page")} and files are erased. This can't be undone.`} action="Delete" onCancel={() => setDeleting(false)} onConfirm={() => { setDeleting(false); remove.mutate(); }} />
      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent className="max-w-md">
          <form onSubmit={submit} className="contents">
            <DialogHeader><DialogTitle>Rename the scan</DialogTitle><DialogDescription className="sr-only">Give this scan a name.</DialogDescription></DialogHeader>
            <DialogBody><Input autoFocus aria-label="The scan's name" maxLength={160} value={draft} onChange={(event) => setDraft(event.target.value)} /></DialogBody>
            <DialogFooter className="justify-end">
              <Button type="button" variant="secondary" onClick={() => setRenaming(false)}>Cancel</Button>
              <Button type="submit" disabled={!draft.trim() || rename.isPending}>Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
  if (row) {
    return (
      <li className={cn("relative flex items-center gap-3 px-3 py-2 transition-colors hover:bg-accent/50", selected && "bg-primary/10")}>
        {tick}
        <span className="size-14 shrink-0 overflow-hidden rounded-md bg-tile p-0.5">{picture}</span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium" title={scan.title}>{name}</h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">{status}</p>
        </div>
        <span className="text-xs text-muted-foreground max-md:hidden">{new Date(scan.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span>
        {menu}
        {dialogs}
      </li>
    );
  }
  return (
    <article className={cn("group relative flex flex-col overflow-hidden rounded-xl border bg-card transition-colors focus-within:border-foreground/30 hover:border-foreground/30", selected ? "border-primary ring-2 ring-primary/50" : "border-border")}>
      <div className="relative aspect-[4/3] bg-tile p-3">
        {picture}
        {tick}
      </div>
      {active && !waiting && <div className="h-1 bg-muted" aria-hidden><div className="h-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} /></div>}
      <div className="flex items-start gap-1 p-3 pr-1.5">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium" title={scan.title}>{name}</h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">{status}</p>
        </div>
        {menu}
      </div>
      {dialogs}
    </article>
  );
}
