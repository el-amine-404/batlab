import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Pencil, Plus, Trash2, Users, Wand2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/common/CopyButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { PermissionPicker, usePermissionLabels } from "@/components/people/PermissionPicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Permission, type User } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { cn, timeAgo } from "@/lib/utils";

const PEOPLE = ["users"] as const;

/** A password to hand over: 16 random letters and digits. */
export function newPassword(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  return Array.from(crypto.getRandomValues(new Uint32Array(16)), (n) => alphabet[n % alphabet.length]).join("");
}

function PasswordField({ id, value, onChange, label = "Password" }: { id: string; value: string; onChange: (v: string) => void; label?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="new-password" spellCheck={false}
               className="font-mono" minLength={10} required />
        <Button type="button" variant="secondary" size="icon" title="Make one" aria-label="Make a password" onClick={() => onChange(newPassword())}>
          <Wand2 />
        </Button>
        <CopyButton text={value} label="password" />
      </div>
      <p className="text-xs text-muted-foreground">At least 10 characters. Give it to them yourself; they can change it in their account.</p>
    </div>
  );
}

/** Adds someone, or changes their name and what they may do. */
function PersonDialog({ person, open, onOpenChange, onSaved }: {
  person?: User;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [permissions, setPermissions] = useState<Permission[]>(["download"]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setUsername(person?.username ?? "");
    setName(person?.name ?? "");
    setPassword(person ? "" : newPassword());
    setPermissions(person?.permissions ?? ["download"]);
  }, [open, person]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (person) {
        await api.updateUser(person.id, { name, permissions });
        toast.success(`${name || person.username} changed`);
      } else {
        await api.createUser({ username, name, password, permissions });
        toast.success(`${name || username} added: give them their password`);
      }
      onSaved();
      onOpenChange(false);
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader>
            <DialogTitle>{person ? `Change ${person.name}` : "Add someone"}</DialogTitle>
            <DialogDescription>{person ? `Signs in as ${person.username}.` : "They sign in with a user name and password of their own."}</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-4">
            {!person && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="person-user">User name</Label>
                <Input id="person-user" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" spellCheck={false}
                       autoComplete="off" required pattern="[A-Za-z0-9._\-]+" maxLength={40} />
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="person-name">Name</Label>
              <Input id="person-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder={username} />
              <p className="text-xs text-muted-foreground">How the page and the notifications name them: "Asked by …".</p>
            </div>
            {!person && <PasswordField id="person-password" value={password} onChange={setPassword} />}
            <div className="flex flex-col gap-1.5">
              <Label>What they may do</Label>
              <PermissionPicker idPrefix="person" value={permissions} onChange={setPermissions} />
            </div>
          </DialogBody>
          <DialogFooter className="justify-end">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || (!person && (!username || password.length < 10))}>
              {busy && <Loader2 className="animate-spin" />} {person ? "Save" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetDialog({ person, onClose }: { person: User | null; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setPassword(person ? newPassword() : ""), [person]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!person) return;
    setBusy(true);
    try {
      await api.resetPassword(person.id, password);
      toast.success(`${person.name}'s password changed: they were signed out`);
      onClose();
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={!!person} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader>
            <DialogTitle>A new password for {person?.name}</DialogTitle>
            <DialogDescription>For a forgotten one. Their sessions end: they sign in again with this.</DialogDescription>
          </DialogHeader>
          <DialogBody><PasswordField id="reset-password" value={password} onChange={setPassword} label="New password" /></DialogBody>
          <DialogFooter className="justify-end">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy || password.length < 10}>{busy && <Loader2 className="animate-spin" />} Change it</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PeoplePage() {
  const client = useQueryClient();
  const me = useMe();
  const labels = usePermissionLabels();
  const { data, error, isPending } = useQuery({ queryKey: PEOPLE, queryFn: api.users });
  const [editing, setEditing] = useState<User | undefined>();
  const [open, setOpen] = useState(false);
  const [resetting, setResetting] = useState<User | null>(null);
  const reload = () => void client.invalidateQueries({ queryKey: PEOPLE });
  const run = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    reload();
  };

  return (
    <>
      <PageHeader title="People"
                  description="Who can sign in, and what each may do. Their downloads carry their name, in the page and in the notifications."
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus /> Add</Button>} />
      {isPending ? <Loading /> : error ? <Failed message={errorText(error)} /> : !data.users.length ? (
        <Empty icon={Users} title="Nobody yet">Add the people who use songdrop.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.users.map((person) => {
            const self = person.id === me.id;
            return (
              <li key={person.id} className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4", !person.enabled && "opacity-60")}>
                <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/20 text-sm font-semibold text-violet-300">
                  {person.name.charAt(0).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <p className="font-medium">{person.name}</p>
                    <span className="text-xs text-muted-foreground">@{person.username}</span>
                    {self && <Badge variant="muted">you</Badge>}
                    {!person.enabled && <Badge variant="muted">turned off</Badge>}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {labels(person.permissions)} · {person.last_login_at ? `signed in ${timeAgo(person.last_login_at)}` : "never signed in"}
                  </p>
                </div>
                <Switch checked={person.enabled} disabled={self} aria-label={person.enabled ? `Turn ${person.name} off` : `Turn ${person.name} on`}
                        title={self ? "You can't turn yourself off" : person.enabled ? "On: can sign in" : "Off: can't sign in"}
                        onCheckedChange={(enabled) => void run(() => api.updateUser(person.id, { enabled }))} />
                <Button variant="ghost" size="icon-sm" title="A new password" aria-label={`A new password for ${person.name}`} onClick={() => setResetting(person)}>
                  <KeyRound />
                </Button>
                <Button variant="ghost" size="icon-sm" aria-label={`Change ${person.name}`} onClick={() => { setEditing(person); setOpen(true); }}>
                  <Pencil />
                </Button>
                <Button variant="ghost" size="icon-sm" disabled={self} aria-label={`Remove ${person.name}`} className="hover:text-destructive"
                        onClick={() => { if (window.confirm(`Remove ${person.name}? Their downloads stay.`)) void run(() => api.deleteUser(person.id), "Removed"); }}>
                  <Trash2 />
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <PersonDialog person={editing} open={open} onOpenChange={setOpen} onSaved={reload} />
      <ResetDialog person={resetting} onClose={() => setResetting(null)} />
    </>
  );
}
