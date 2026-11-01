import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errorText } from "@/lib/api";

/** A scan's name, renamed where it stands. */
export function ScanTitle({ id, title }: { id: string; title: string }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (editing) input.current?.select(); }, [editing]);
  const save = useMutation({
    mutationFn: (next: string) => api.editProject(id, { title: next }),
    onSuccess: (project) => {
      client.setQueryData(["project", id], project);
      void client.invalidateQueries({ queryKey: ["projects"] });
      setEditing(false);
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const submit = () => {
    const next = draft.trim();
    if (!next || next === title) return setEditing(false);
    save.mutate(next);
  };
  if (!editing) {
    return (
      <button type="button" onClick={() => { setDraft(title); setEditing(true); }} title="Rename this scan"
              className="group flex max-w-full min-w-0 items-center gap-2 rounded-md text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring">
        <h1 className="min-w-0 text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">{title}</h1>
        <Pencil className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" aria-hidden />
        <span className="sr-only">Rename</span>
      </button>
    );
  }
  return (
    <form className="flex max-w-xl items-center gap-2" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <Input ref={input} value={draft} maxLength={160} autoFocus aria-label="The scan's name" className="h-10 text-lg font-semibold"
             onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setEditing(false); }} />
      <Button type="submit" size="icon" aria-label="Save the name" disabled={save.isPending}><Check /></Button>
      <Button type="button" variant="secondary" size="icon" aria-label="Keep the old name" onClick={() => setEditing(false)}><X /></Button>
    </form>
  );
}
