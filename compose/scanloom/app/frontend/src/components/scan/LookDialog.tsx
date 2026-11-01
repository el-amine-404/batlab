import { useMutation } from "@tanstack/react-query";
import { Check, Loader2, Palette, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, errorText, type Profile, type Project } from "@/lib/api";
import { cn, plural } from "@/lib/utils";

import { PROFILES } from "./labels";

/** The one place that transforms a whole scan: what each look does, on its own first page, before any of it happens. */
export function LookDialog({ project, sample, onClose, onChanged }: { project: Project; sample: string | null; onClose: () => void; onChanged: () => void }) {
  const [chosen, setChosen] = useState<Profile>(project.profile);
  const apply = useMutation({
    mutationFn: () => api.editProject(project.public_id, { profile: chosen }),
    onSuccess: () => {
      onChanged();
      toast.success(`Updating ${plural(project.completed_count, "page")}: the progress is on this page`);
      onClose();
    },
  });
  const changed = chosen !== project.profile;
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Palette className="size-4" /> Change the look of all pages</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-muted-foreground">
            This transforms the {plural(project.completed_count, "finished page")}, and takes a moment. The photos are kept, so it can be changed back. A page given its own look stays as it is.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div role="radiogroup" aria-label="Look" className="grid gap-3 sm:grid-cols-3">
            {PROFILES.map(({ id, label, hint, filter }) => (
              <button key={id} type="button" role="radio" aria-checked={chosen === id} onClick={() => setChosen(id)}
                      className={cn("flex flex-col overflow-hidden rounded-lg border text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring", chosen === id ? "border-primary ring-1 ring-primary" : "border-border hover:border-foreground/40")}>
                <span className="block aspect-[4/3] bg-tile p-2">
                  {sample && <img src={sample} alt="" className="size-full object-contain" style={{ filter }} />}
                </span>
                <span className="flex flex-col gap-0.5 p-2.5">
                  <span className="flex items-center gap-1.5 text-sm font-medium">{label}{project.profile === id && <span className="text-xs font-normal text-muted-foreground">now</span>}{chosen === id && <Check className="ml-auto size-4 text-primary" />}</span>
                  <span className="text-xs text-muted-foreground">{hint}</span>
                </span>
              </button>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">The small pictures are a sketch of each look. The pages themselves are made on the server.</p>
          {apply.error && <p className="mt-2 flex items-start gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {errorText(apply.error)}</p>}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={!changed || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? <Loader2 className="animate-spin" /> : <Palette />} {changed ? `Change ${plural(project.completed_count, "page")} to ${PROFILES.find(({ id }) => id === chosen)?.label}` : "Choose a look"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
