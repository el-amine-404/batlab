import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileImage, Loader2, MoreVertical, Pencil, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { api, errorText, type ProjectSummary } from "@/lib/api";
import { plural, timeAgo } from "@/lib/utils";

export function ScanCard({ scan }: { scan: ProjectSummary }) {
  const client = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [draft, setDraft] = useState(scan.title);
  const refresh = () => void client.invalidateQueries({ queryKey: ["projects"] });
  const rename = useMutation({ mutationFn: (title: string) => api.editProject(scan.public_id, { title }), onSuccess: () => { refresh(); setRenaming(false); }, onError: (error) => toast.error(errorText(error)) });
  const remove = useMutation({ mutationFn: () => api.deleteProject(scan.public_id), onSuccess: () => { refresh(); toast.success(`${scan.title} deleted`); }, onError: (error) => toast.error(errorText(error)) });
  const active = scan.status === "processing";
  const percent = scan.page_count ? Math.round(((scan.completed_count + scan.failed_count) / scan.page_count) * 100) : 0;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.trim()) rename.mutate(draft.trim());
  };
  return (
    <article className="group relative flex flex-col overflow-hidden rounded-xl border border-border bg-card transition-colors focus-within:border-foreground/30 hover:border-foreground/30">
      <div className="relative aspect-[4/3] bg-tile p-3">
        {scan.thumbnail_url ? <img src={scan.thumbnail_url} alt="" loading="lazy" decoding="async" className="size-full object-contain drop-shadow-md" />
          : <span className="flex size-full items-center justify-center text-muted-foreground">{active ? <Loader2 className="size-6 animate-spin" /> : <FileImage className="size-6" />}</span>}
      </div>
      {active && <div className="h-1 bg-muted" aria-hidden><div className="h-full bg-primary transition-[width] duration-500" style={{ width: `${percent}%` }} /></div>}
      <div className="flex items-start gap-1 p-3 pr-1.5">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium" title={scan.title}>
            {/* The whole card is this link. */}
            <Link to={`/scans/${scan.public_id}`} className="outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:after:ring-[3px] focus-visible:after:ring-ring focus-visible:after:ring-inset">{scan.title}</Link>
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <span>{active ? `${scan.completed_count + scan.failed_count} of ${plural(scan.page_count, "page")}` : plural(scan.page_count, "page")}</span>
            <span>{timeAgo(scan.created_at)}</span>
            {scan.failed_count > 0 && <span className="flex items-center gap-1 text-destructive"><TriangleAlert className="size-3" /> {scan.failed_count} failed</span>}
            {!scan.failed_count && scan.warning_count > 0 && <span className="flex items-center gap-1 text-warning"><TriangleAlert className="size-3" /> {scan.warning_count}</span>}
          </p>
        </div>
        <Menu>
          <MenuTrigger asChild><Button variant="ghost" size="icon-sm" className="relative z-10 shrink-0" aria-label={`More for ${scan.title}`}><MoreVertical /></Button></MenuTrigger>
          <MenuContent>
            <MenuItem onSelect={() => { setDraft(scan.title); setRenaming(true); }}><Pencil /> Rename</MenuItem>
            <MenuItem danger onSelect={() => setDeleting(true)}><Trash2 /> Delete</MenuItem>
          </MenuContent>
        </Menu>
      </div>
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
    </article>
  );
}
