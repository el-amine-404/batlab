import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppWindow, KeyRound, Plus, ShieldCheck, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import { api, type Account, type Role } from "../api";
import { Badge, Button, Card, CodeBlock, ErrorNote, Field, Muted, PasswordField, Select, SelectField, formatWhen } from "../components/ui";

const ROLE: Record<Role, { label: string; text: string }> = {
  ADMIN: { label: "Admin", text: "Everything, and the accounts." },
  USER: { label: "User", text: "Adds files and manages their own documents." },
  READ_ONLY: { label: "Read-only", text: "Sees their documents; adds nothing." },
  ENRICHMENT: { label: "AI agent", text: "Only the laptop's OCR and suggestions." },
};
const PERSON_ROLES: Role[] = ["ADMIN", "USER", "READ_ONLY"];
const APP_ROLES: Role[] = ["READ_ONLY", "USER", "ENRICHMENT", "ADMIN"];

const failed = (error: unknown) => toast.error(error instanceof Error ? error.message : "That didn't work.");

/** A button that asks once more: the first click arms it for a few seconds. */
function ConfirmButton({ children, confirm, onConfirm, disabled }: { children: ReactNode; confirm: string; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const timer = window.setTimeout(() => setArmed(false), 4000); return () => window.clearTimeout(timer); }, [armed]);
  return (
    <Button size="sm" variant={armed ? "danger" : "ghost"} disabled={disabled} onClick={() => { if (armed) { setArmed(false); onConfirm(); } else setArmed(true); }}>
      {armed ? confirm : children}
    </Button>
  );
}

/** One's own password: the other sessions end when it changes. */
export function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => { setCurrent(""); setNext(""); toast.success("Password changed. Your other sessions have ended."); },
  });
  const submit = (event: FormEvent) => { event.preventDefault(); change.mutate(); };
  return (
    <Card title="Your password" description="Changing it signs you out everywhere else." icon={<KeyRound className="size-5" />}>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <PasswordField label="Current password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <PasswordField label="New password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
        <Button type="submit" disabled={change.isPending || !current || next.length < 10}>Change it</Button>
      </form>
      <div className="mt-3"><ErrorNote error={change.error} /></div>
    </Card>
  );
}

/** Admins: people (a name and a password) and apps (API keys), and what each may do. */
export function AccountsCard({ me }: { me: string }) {
  const client = useQueryClient();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const [shownKey, setShownKey] = useState<{ account: string; label: string; token: string } | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ["accounts"] });
  const people = accounts.data?.filter((a) => a.signsIn) ?? [];
  const apps = accounts.data?.filter((a) => !a.signsIn) ?? [];

  return (
    <>
      <Card title="People" description="They sign in on this page with their name and password." icon={<Users className="size-5" />}
        actions={<AddPerson onDone={refresh} />}>
        <ErrorNote error={accounts.error} />
        <ul className="divide-y divide-border" data-testid="people">
          {people.map((account) => <PersonRow key={account.id} account={account} self={account.username === me} onChange={refresh} />)}
        </ul>
      </Card>
      <Card title="Apps and API keys" icon={<AppWindow className="size-5" />}
        description={<>Homepage, the laptop's AI agent, scripts: each sends its key as <code className="text-foreground">Authorization: Bearer …</code></>}
        actions={<AddApp onDone={(account, token) => { refresh(); setShownKey({ account: account.username, label: "key", token }); }} />}>
        {shownKey && (
          <div className="mb-4 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm" role="status" data-testid="new-key">
            <p className="mb-2 font-medium text-warning">The key for {shownKey.account} ({shownKey.label}): copy it now, it isn't shown again.</p>
            <CodeBlock code={shownKey.token} label="key" />
            <Button size="sm" variant="ghost" className="mt-2" onClick={() => setShownKey(null)}>I've copied it</Button>
          </div>
        )}
        <ul className="divide-y divide-border" data-testid="apps">
          {apps.map((account) => <AppRow key={account.id} account={account} onChange={refresh}
            onKey={(label, token) => setShownKey({ account: account.username, label, token })} />)}
        </ul>
      </Card>
    </>
  );
}

function RoleSelect({ value, roles, onChange, label }: { value: Role; roles: Role[]; onChange: (role: Role) => void; label: string }) {
  return (
    <Select className="h-8 w-36" aria-label={label} value={value} onChange={(e) => onChange(e.target.value as Role)}>
      {roles.map((role) => <option key={role} value={role}>{ROLE[role].label}</option>)}
    </Select>
  );
}

function Avatar({ name, app }: { name: string; app?: boolean }) {
  return (
    <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
      {app ? <AppWindow className="size-4" /> : name.charAt(0).toUpperCase()}
    </span>
  );
}

function KeyLine({ account, keyId, label, lastUsedAt, onChange }: { account: Account; keyId: string; label: string; lastUsedAt: string | null; onChange: () => void }) {
  const revoke = useMutation({
    mutationFn: () => api.revokeKey(account.id, keyId),
    onSuccess: () => { toast.success(`Key "${label}" revoked.`); onChange(); }, onError: failed,
  });
  return (
    <li className="flex items-center gap-2 text-sm">
      <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{label} <span className="text-xs text-muted-foreground">· {lastUsedAt ? `used ${formatWhen(lastUsedAt)}` : "never used"}</span></span>
      <ConfirmButton confirm="Revoke it" onConfirm={() => revoke.mutate()} disabled={revoke.isPending}>Revoke</ConfirmButton>
    </li>
  );
}

function PersonRow({ account, self, onChange }: { account: Account; self: boolean; onChange: () => void }) {
  const [password, setPassword] = useState<string | null>(null);
  const update = useMutation({
    mutationFn: (body: { role?: Role; enabled?: boolean; password?: string }) => api.updateAccount(account.id, body),
    onSuccess: (_, body) => {
      setPassword(null); onChange();
      toast.success(body.password ? `New password set for ${account.username}; their sessions ended.`
        : body.enabled !== undefined ? `${account.username} ${body.enabled ? "can sign in again" : "is turned off"}.` : `${account.username} is now ${ROLE[body.role!].label}.`);
    },
    onError: failed,
  });
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-3">
        <Avatar name={account.username} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">{account.username}
            {self && <Badge>you</Badge>}{!account.enabled && <Badge tone="warn">off</Badge>}</p>
          <p className="text-xs text-muted-foreground">{ROLE[account.role].text} · {account.lastLoginAt ? `signed in ${formatWhen(account.lastLoginAt)}` : "never signed in"}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <RoleSelect label={`Role of ${account.username}`} value={account.role} roles={PERSON_ROLES} onChange={(role) => update.mutate({ role })} />
          <Button size="sm" variant="secondary" onClick={() => setPassword(password === null ? "" : null)}>Password</Button>
          {!self && <Button size="sm" variant="ghost" onClick={() => update.mutate({ enabled: !account.enabled })}>{account.enabled ? "Turn off" : "Turn on"}</Button>}
        </div>
      </div>
      {/* Keys made before passwords (the old access token): they still sign scripts in as this person. */}
      {account.keys.length > 0 && (
        <ul className="mt-2 ml-12 space-y-1">
          {account.keys.map((key) => <KeyLine key={key.id} account={account} keyId={key.id} label={`API key "${key.label}"`} lastUsedAt={key.lastUsedAt} onChange={onChange} />)}
        </ul>
      )}
      {password !== null && (
        <form className="mt-3 ml-12 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); update.mutate({ password }); }}>
          <div className="min-w-56 flex-1">
            <PasswordField label={`New password for ${account.username}`} autoComplete="new-password" minLength={10} required
              hint="Their sessions end; tell them the new one." value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <Button type="submit" disabled={update.isPending || password.length < 10}>Set it</Button>
        </form>
      )}
    </li>
  );
}

function AppRow({ account, onChange, onKey }: { account: Account; onChange: () => void; onKey: (label: string, token: string) => void }) {
  const [label, setLabel] = useState("");
  const update = useMutation({
    mutationFn: (body: { role?: Role; enabled?: boolean }) => api.updateAccount(account.id, body),
    onSuccess: (_, body) => { onChange(); toast.success(body.enabled !== undefined ? `${account.username} ${body.enabled ? "is on" : "is turned off"}.` : `${account.username} is now ${ROLE[body.role!].label}.`); },
    onError: failed,
  });
  const add = useMutation({
    mutationFn: () => api.addKey(account.id, label.trim() || "key"),
    onSuccess: (key) => { setLabel(""); onKey(key.label, key.token); onChange(); }, onError: failed,
  });
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-3">
        <Avatar name={account.username} app />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">{account.username}{!account.enabled && <Badge tone="warn">off</Badge>}</p>
          <p className="text-xs text-muted-foreground">{ROLE[account.role].text}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <RoleSelect label={`Role of ${account.username}`} value={account.role} roles={APP_ROLES} onChange={(role) => update.mutate({ role })} />
          <Button size="sm" variant="ghost" onClick={() => update.mutate({ enabled: !account.enabled })}>{account.enabled ? "Turn off" : "Turn on"}</Button>
        </div>
      </div>
      <ul className="mt-2 ml-12 space-y-1">
        {account.keys.map((key) => <KeyLine key={key.id} account={account} keyId={key.id} label={key.label} lastUsedAt={key.lastUsedAt} onChange={onChange} />)}
        {account.keys.length === 0 && <li className="text-xs text-muted-foreground">No key: it can't sign in.</li>}
      </ul>
      <form className="mt-2 ml-12 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="h-8 min-w-40 flex-1 rounded-md border border-input bg-background/60 px-2.5 text-sm outline-none focus-visible:border-ring" aria-label="New key's name"
          placeholder="New key's name, e.g. iPhone shortcut" value={label} onChange={(e) => setLabel(e.target.value)} />
        <Button type="submit" size="sm" variant="secondary" disabled={add.isPending}><Plus /> New key</Button>
      </form>
    </li>
  );
}

function AddPerson({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("USER");
  const create = useMutation({
    mutationFn: () => api.createAccount({ username: username.trim(), role, password }),
    onSuccess: (account) => { setUsername(""); setPassword(""); setRole("USER"); setOpen(false); onDone(); toast.success(`${account.username} can sign in now.`); },
  });
  if (!open) return <Button size="sm" variant="secondary" onClick={() => setOpen(true)}><UserPlus /> Add a person</Button>;
  return (
    <form className="w-full space-y-3 rounded-lg border border-border bg-background/40 p-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }} data-testid="add-person">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="User name" name="new-username" autoComplete="off" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
        <PasswordField label="Password" name="new-person-password" autoComplete="new-password" minLength={10} required hint="At least 10 characters; tell them." value={password} onChange={(e) => setPassword(e.target.value)} />
        <SelectField label="Role" name="new-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {PERSON_ROLES.map((value) => <option key={value} value={value}>{ROLE[value].label}</option>)}
        </SelectField>
      </div>
      <Muted className="flex items-center gap-1.5"><ShieldCheck className="size-4" /> {ROLE[role].text}</Muted>
      <ErrorNote error={create.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending || !username.trim() || password.length < 10}>Add</Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}

function AddApp({ onDone }: { onDone: (account: Account, token: string) => void }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("READ_ONLY");
  const create = useMutation({
    mutationFn: async () => {
      const account = await api.createAccount({ username: username.trim(), role });
      return api.addKey(account.id, "key");
    },
    onSuccess: (key) => { setUsername(""); setRole("READ_ONLY"); setOpen(false); onDone(key.account, key.token); },
  });
  if (!open) return <Button size="sm" variant="secondary" onClick={() => setOpen(true)}><Plus /> Add an app</Button>;
  return (
    <form className="w-full space-y-3 rounded-lg border border-border bg-background/40 p-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" placeholder="e.g. homepage" autoComplete="off" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
        <SelectField label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {APP_ROLES.map((value) => <option key={value} value={value}>{ROLE[value].label}</option>)}
        </SelectField>
      </div>
      <Muted>{ROLE[role].text} Its key is shown once, next.</Muted>
      <ErrorNote error={create.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending || !username.trim()}>Add and make a key</Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}
