import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Pencil, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/common/CopyButton";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { PermissionPicker, usePermissionLabels } from "@/components/people/PermissionPicker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type ApiKey, type ApiKeyMade, errorText, type Permission } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const KEYS = ["api-keys"] as const;

/** Makes a key (then shows it, once), or changes what one may do. */
function KeyDialog({ saved, open, onOpenChange, onSaved }: {
  saved?: ApiKey;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [permissions, setPermissions] = useState<Permission[]>(["download", "destinations"]);
  const [made, setMade] = useState<ApiKeyMade | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(saved?.name ?? "");
    setPermissions(saved?.permissions ?? ["download", "destinations"]);
    setMade(null);
  }, [open, saved]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (saved) {
        await api.updateKey(saved.id, permissions);
        toast.success(`${saved.name} changed`);
        onOpenChange(false);
      } else {
        setMade(await api.createKey(name, permissions));
      }
      onSaved();
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  if (made) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{made.name}'s key</DialogTitle>
            <DialogDescription>Copy it into the app now: it is shown this once, and videodrop keeps only a fingerprint of it.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-black/30 p-2 pl-3">
              <code className="min-w-0 flex-1 font-mono text-sm break-all select-all">{made.key}</code>
              <CopyButton text={made.key} label="key" />
            </div>
            <p className="text-xs text-muted-foreground">
              The app sends it with each request, as <code className="text-foreground">X-Api-Key: {made.prefix}…</code> (or
              <code className="text-foreground"> Authorization: Bearer …</code>). Lost: remove this key and make another.
            </p>
          </DialogBody>
          <DialogFooter className="justify-end"><Button onClick={() => onOpenChange(false)}>Done</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader>
            <DialogTitle>{saved ? `Change ${saved.name}` : "Make an API key"}</DialogTitle>
            <DialogDescription>For an app that uses videodrop: clipseerr, Homepage, a script. Its downloads carry its name.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-4">
            {!saved && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="key-name">Name</Label>
                <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="clipseerr" required maxLength={40} />
                <p className="text-xs text-muted-foreground">The app it is for: notifications say "Asked by {name || "…"}".</p>
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label>What it may do</Label>
              <PermissionPicker idPrefix="key" value={permissions} onChange={setPermissions} allowAdmin={false} />
              <p className="text-xs text-muted-foreground">With none ticked it can only read: Homepage's counts need "See everyone's downloads".</p>
            </div>
          </DialogBody>
          <DialogFooter className="justify-end">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || (!saved && !name.trim())}>{busy && <Loader2 className="animate-spin" />} {saved ? "Save" : "Make it"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function KeysPage() {
  const client = useQueryClient();
  const labels = usePermissionLabels();
  const { data, error, isPending, refetch } = useQuery({ queryKey: KEYS, queryFn: api.keys });
  const [editing, setEditing] = useState<ApiKey | undefined>();
  const [open, setOpen] = useState(false);
  const reload = () => void client.invalidateQueries({ queryKey: KEYS });

  const remove = async (key: ApiKey) => {
    if (!window.confirm(`Remove ${key.name}'s key? The app using it is refused from now on.`)) return;
    try {
      await api.deleteKey(key.id);
      toast.success("Removed");
    } catch (caught) {
      toast.error(errorText(caught));
    }
    reload();
  };

  return (
    <>
      <PageHeader eyebrow="Settings" title="API keys"
                  description="How other apps use videodrop: each has a key of its own, with what it may do. A key can't manage people or keys."
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus /> Make one</Button>} />
      {isPending ? <Loading /> : error ? <Failed error={error} retry={() => void refetch()} /> : !data.keys.length ? (
        <Empty icon={<KeyRound />} title="No key yet">Apps can't use videodrop without one.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.keys.map((key) => (
            <li key={key.id} className="flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
              <KeyRound className="size-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <p className="font-medium">{key.name}</p>
                  <code className="text-xs text-muted-foreground" title="The key's first characters, to recognise it">{key.prefix}…</code>
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {labels(key.permissions)} · {key.last_used_at ? (
                    <>
                      used {timeAgo(key.last_used_at)}
                      {key.last_address && (
                        // The name says which app or device; the address is kept for whoever needs it.
                        <span title={key.last_address}> by {key.last_host ?? key.last_address}</span>
                      )}
                    </>
                  ) : "never used"}
                </p>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label={`Change ${key.name}`} onClick={() => { setEditing(key); setOpen(true); }}><Pencil /></Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Remove ${key.name}`} className="hover:text-destructive" onClick={() => void remove(key)}><Trash2 /></Button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-6 flex items-start gap-2 text-xs text-muted-foreground">
        <ShieldAlert className="mt-px size-3.5 shrink-0" />
        A key is a password for an app: keep it in the app's settings or its secrets, not in a shared file. Only API keys may ask for callbacks.
      </p>
      <KeyDialog saved={editing} open={open} onOpenChange={setOpen} onSaved={reload} />
    </>
  );
}
