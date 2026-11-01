import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileImage, Loader2, Plus, ScanLine, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { api, errorText, type Page, type Project } from "@/lib/api";
import { eta, formatBytes, plural } from "@/lib/utils";

/**
 * What was given, before anything is scanned: every photo as the server sees it
 * after its security check, so HEIC too. Remove any, then scan or cancel.
 */
export function Review({ project, onAdd }: { project: Project; onAdd: () => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const id = project.public_id;
  const [cancelling, setCancelling] = useState(false);
  const [look, setLook] = useState<Page | null>(null);
  const list = useQuery({
    queryKey: ["pages", id, "held"], queryFn: () => api.pages(id, 1000, 0, "held"),
    refetchInterval: (query) => (query.state.data?.items.some((page) => isChecking(page)) ? 1500 : false),
  });
  const pages = list.data?.items ?? [];
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["project", id] });
    void client.invalidateQueries({ queryKey: ["pages", id] });
    void client.invalidateQueries({ queryKey: ["projects"] });
  };
  const failure = (error: unknown) => toast.error(errorText(error));
  const remove = useMutation({ mutationFn: (ids: string[]) => api.deletePages(id, ids), onSuccess: refresh, onError: failure });
  const start = useMutation({ mutationFn: () => api.start(id), onSuccess: refresh, onError: failure });
  const cancelAll = useMutation({
    mutationFn: async () => {
      if (project.page_count === pages.length) await api.deleteProject(id);
      else await api.deletePages(id, pages.map((page) => page.id));
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      if (project.page_count === pages.length) navigate("/");
      else refresh();
    },
    onError: failure,
  });
  const checking = pages.filter(isChecking).length;
  const held = pages.filter((page) => page.status === "held");
  const copies = held.filter((page) => page.reused_from?.alone);
  const ready = held.filter((page) => !page.reused_from?.alone);
  const fresh = ready.filter((page) => !page.reused_from).length;

  return (
    <section aria-label="Photos to review" className="mb-6">
      <div className="sticky top-14 z-20 mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-card p-3 lg:top-2">
        <div className="min-w-0">
          <h2 className="font-semibold">{checking ? `Checking ${plural(checking, "photo")}…` : ready.length ? `${plural(ready.length, "photo")} ready to scan` : copies.length ? "Already scanned" : "Nothing to scan"}</h2>
          <p className="text-sm text-muted-foreground">
            {!ready.length && copies.length && !checking ? "You have already scanned this. Open that scan, or cancel." : <>Nothing is scanned until you say.{fresh > 0 && ` It will take ${eta(fresh * project.page_seconds)}.`}{copies.length > 0 && ` ${plural(copies.length, "photo")} already scanned on its own will be left out.`}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={onAdd}><Plus /> Add more</Button>
          <Button variant="secondary" onClick={() => setCancelling(true)}><X /> Cancel all</Button>
          <Button disabled={!ready.length || checking > 0 || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? <Loader2 className="animate-spin" /> : <ScanLine />} Scan {plural(ready.length, "photo")}
          </Button>
        </div>
      </div>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
        {pages.map((page) => (
          <li key={page.id} className="relative overflow-hidden rounded-xl border border-border bg-card">
            <button type="button" disabled={!page.preview_url} onClick={() => setLook(page)} aria-label={`Look at ${page.original_name}`}
                    className="block aspect-[3/4] w-full bg-tile p-1.5 outline-none focus-visible:ring-[3px] focus-visible:ring-inset focus-visible:ring-ring">
              {page.thumb_url ? <img src={page.thumb_url} alt="" loading="lazy" className="size-full object-contain" />
                : page.status === "blocked" || page.status === "failed" ? <span className="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs text-destructive"><ShieldAlert className="size-5" /> Refused</span>
                : <span className="flex size-full flex-col items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-5 animate-spin" /> Security check</span>}
            </button>
            <button type="button" aria-label={`Remove ${page.original_name}`} title="Remove this photo" onClick={() => remove.mutate([page.id])}
                    className="absolute top-2 right-2 flex size-8 items-center justify-center rounded-lg bg-black/75 text-white transition-colors hover:bg-black focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none"><X className="size-4" /></button>
            <div className="border-t border-border px-2.5 py-2">
              <p className="flex items-center gap-1.5 truncate text-xs font-medium" title={page.original_name}><FileImage className="size-3.5 shrink-0 text-muted-foreground" aria-hidden /><span className="truncate">{page.original_name}</span></p>
              {page.error ? <p className="mt-0.5 line-clamp-2 text-xs text-destructive">{page.error}</p>
                : page.reused_from?.alone ? (
                  <p className="mt-0.5 flex items-start gap-1 text-xs text-warning"><CheckCircle2 className="mt-px size-3.5 shrink-0" />
                    <span>Already scanned: <Link className="underline underline-offset-2" to={`/scans/${page.reused_from.project}`}>{page.reused_from.title}</Link></span></p>
                ) : <p className="mt-0.5 text-xs text-muted-foreground">{page.width && page.height ? `${page.width} × ${page.height} · ` : ""}{formatBytes(page.source_bytes)}</p>}
            </div>
          </li>
        ))}
      </ul>
      <Dialog open={Boolean(look)} onOpenChange={(open) => { if (!open) setLook(null); }}>
        <DialogContent className="max-h-[94dvh] max-w-5xl overflow-hidden p-2">
          <DialogTitle className="truncate px-2 py-1 pr-10 text-sm">{look?.original_name}</DialogTitle>
          <DialogDescription className="sr-only">The photo as uploaded.</DialogDescription>
          {look?.preview_url && <img src={look.preview_url} alt={look.original_name} className="min-h-0 w-full flex-1 object-contain" />}
        </DialogContent>
      </Dialog>
      <Confirm open={cancelling} title="Cancel these photos?" text={`${plural(pages.length, "photo")} will be removed without being scanned.`} action="Cancel them"
               onCancel={() => setCancelling(false)} onConfirm={() => { setCancelling(false); cancelAll.mutate(); }} />
    </section>
  );
}

const isChecking = (page: Page) => !["held", "failed", "blocked"].includes(page.status);
