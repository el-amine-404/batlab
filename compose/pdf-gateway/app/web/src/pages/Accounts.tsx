import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Account, type Role } from "../api";
import { Badge, Button, Card, ErrorNote, Field, Muted, PasswordField, formatWhen } from "../components/ui";

const ROLE: Record<Role, { label: string; text: string }> = {
  ADMIN: { label: "Admin", text: "Everything, and the accounts." },
  USER: { label: "User", text: "Adds files and manages their own documents." },
  READ_ONLY: { label: "Read-only", text: "Sees their documents; adds nothing." },
  ENRICHMENT: { label: "AI agent", text: "Only the laptop's OCR and suggestions." },
};
const PERSON_ROLES: Role[] = ["ADMIN", "USER", "READ_ONLY"];
const APP_ROLES: Role[] = ["READ_ONLY", "USER", "ENRICHMENT", "ADMIN"];

const select = "block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base sm:text-sm dark:border-slate-700 dark:bg-slate-950";

/** One's own password: the other sessions end when it changes. */
export function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => { setCurrent(""); setNext(""); setDone(true); },
  });
  const submit = (event: FormEvent) => { event.preventDefault(); setDone(false); change.mutate(); };
  return (
    <Card title="Your password">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <PasswordField label="Current password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <PasswordField label="New password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
        <Button type="submit" disabled={change.isPending || !current || next.length < 10}>Change it</Button>
      </form>
      <div className="mt-3"><ErrorNote error={change.error} /></div>
      {done && <Muted>Changed. Your other sessions have ended.</Muted>}
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
      <Card title="People">
        <Muted className="mb-3">They sign in on this page with their name and password.</Muted>
        <ErrorNote error={accounts.error} />
        <ul className="divide-y divide-slate-200 dark:divide-slate-800" data-testid="people">
          {people.map((account) => <PersonRow key={account.id} account={account} self={account.username === me} onChange={refresh} />)}
        </ul>
        <AddPerson onDone={refresh} />
      </Card>
      <Card title="Apps and API keys">
        <Muted className="mb-3">Homepage, the laptop's AI agent, scripts: each signs in with a key, sent as <code>Authorization: Bearer …</code>.</Muted>
        {shownKey && (
          <div className="mb-3 rounded-lg bg-amber-50 p-3 text-sm ring-1 ring-amber-200 dark:bg-amber-950 dark:ring-amber-900" role="status" data-testid="new-key">
            <p className="font-medium">The key for {shownKey.account} ({shownKey.label}): copy it now, it isn't shown again.</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded bg-white px-2 py-1 text-xs dark:bg-slate-900">{shownKey.token}</code>
              <Button variant="secondary" onClick={() => void navigator.clipboard?.writeText(shownKey.token)}>Copy</Button>
              <Button variant="ghost" onClick={() => setShownKey(null)}>Done</Button>
            </div>
          </div>
        )}
        <ul className="divide-y divide-slate-200 dark:divide-slate-800" data-testid="apps">
          {apps.map((account) => <AppRow key={account.id} account={account} onChange={refresh}
            onKey={(label, token) => setShownKey({ account: account.username, label, token })} />)}
        </ul>
        <AddApp onDone={(account, token) => { refresh(); setShownKey({ account: account.username, label: "key", token }); }} />
      </Card>
    </>
  );
}

function RoleSelect({ value, roles, onChange, label }: { value: Role; roles: Role[]; onChange: (role: Role) => void; label: string }) {
  return (
    <select className={select} aria-label={label} value={value} onChange={(e) => onChange(e.target.value as Role)}>
      {roles.map((role) => <option key={role} value={role}>{ROLE[role].label}</option>)}
    </select>
  );
}

function PersonRow({ account, self, onChange }: { account: Account; self: boolean; onChange: () => void }) {
  const [password, setPassword] = useState<string | null>(null);
  const update = useMutation({
    mutationFn: (body: { role?: Role; enabled?: boolean; password?: string }) => api.updateAccount(account.id, body),
    onSuccess: () => { setPassword(null); onChange(); },
  });
  const revoke = useMutation({ mutationFn: (keyId: string) => api.revokeKey(account.id, keyId), onSuccess: onChange });
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{account.username}</span>
        {self && <Badge>you</Badge>}
        {!account.enabled && <Badge tone="warn">off</Badge>}
        <span className="text-xs text-slate-500 dark:text-slate-400">{account.lastLoginAt ? `last signed in ${formatWhen(account.lastLoginAt)}` : "never signed in"}</span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="w-36"><RoleSelect label={`Role of ${account.username}`} value={account.role} roles={PERSON_ROLES} onChange={(role) => update.mutate({ role })} /></div>
          <Button variant="secondary" onClick={() => setPassword(password === null ? "" : null)}>Password</Button>
          {!self && <Button variant={account.enabled ? "ghost" : "secondary"} onClick={() => update.mutate({ enabled: !account.enabled })}>{account.enabled ? "Turn off" : "Turn on"}</Button>}
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{ROLE[account.role].text}</p>
      {/* Keys made before passwords (the old access token): they still sign scripts in as this person. */}
      {account.keys.map((key) => (
        <p key={key.id} className="mt-1 flex items-center gap-2 text-sm">
          <span className="min-w-0 flex-1 truncate">API key "{key.label}" <span className="text-xs text-slate-500 dark:text-slate-400">· {key.lastUsedAt ? `used ${formatWhen(key.lastUsedAt)}` : "never used"}</span></span>
          <Button variant="ghost" onClick={() => { if (confirm(`Revoke the key "${key.label}" of ${account.username}? What uses it stops working.`)) revoke.mutate(key.id); }}>Revoke</Button>
        </p>
      ))}
      {password !== null && (
        <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); update.mutate({ password }); }}>
          <div className="min-w-56 flex-1">
            <PasswordField label={`New password for ${account.username}`} autoComplete="new-password" minLength={10} required
              hint="Their sessions end; tell them the new one." value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <Button type="submit" disabled={update.isPending || password.length < 10}>Set it</Button>
        </form>
      )}
      <div className="mt-2"><ErrorNote error={update.error ?? revoke.error} /></div>
    </li>
  );
}

function AppRow({ account, onChange, onKey }: { account: Account; onChange: () => void; onKey: (label: string, token: string) => void }) {
  const [label, setLabel] = useState("");
  const update = useMutation({ mutationFn: (body: { role?: Role; enabled?: boolean }) => api.updateAccount(account.id, body), onSuccess: onChange });
  const add = useMutation({
    mutationFn: () => api.addKey(account.id, label.trim() || "key"),
    onSuccess: (key) => { setLabel(""); onKey(key.label, key.token); onChange(); },
  });
  const revoke = useMutation({ mutationFn: (keyId: string) => api.revokeKey(account.id, keyId), onSuccess: onChange });
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{account.username}</span>
        {!account.enabled && <Badge tone="warn">off</Badge>}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="w-36"><RoleSelect label={`Role of ${account.username}`} value={account.role} roles={APP_ROLES} onChange={(role) => update.mutate({ role })} /></div>
          <Button variant={account.enabled ? "ghost" : "secondary"} onClick={() => update.mutate({ enabled: !account.enabled })}>{account.enabled ? "Turn off" : "Turn on"}</Button>
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{ROLE[account.role].text}</p>
      <ul className="mt-2 space-y-1 text-sm">
        {account.keys.map((key) => (
          <li key={key.id} className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate">{key.label} <span className="text-xs text-slate-500 dark:text-slate-400">· {key.lastUsedAt ? `used ${formatWhen(key.lastUsedAt)}` : "never used"}</span></span>
            <Button variant="ghost" onClick={() => { if (confirm(`Revoke the key "${key.label}" of ${account.username}? What uses it stops working.`)) revoke.mutate(key.id); }}>Revoke</Button>
          </li>
        ))}
        {account.keys.length === 0 && <li className="text-xs text-slate-500 dark:text-slate-400">No key: it can't sign in.</li>}
      </ul>
      <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <div className="min-w-48 flex-1"><Field label="New key's name" placeholder="e.g. iPhone shortcut" value={label} onChange={(e) => setLabel(e.target.value)} /></div>
        <Button type="submit" variant="secondary" disabled={add.isPending}>New key</Button>
      </form>
      <div className="mt-2"><ErrorNote error={update.error ?? add.error ?? revoke.error} /></div>
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
    onSuccess: () => { setUsername(""); setPassword(""); setRole("USER"); setOpen(false); onDone(); },
  });
  if (!open) return <Button variant="secondary" className="mt-3" onClick={() => setOpen(true)}>Add a person</Button>;
  return (
    <form className="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800"
      onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="User name" autoComplete="off" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
        <PasswordField label="Password" autoComplete="new-password" minLength={10} required hint="At least 10 characters; tell them." value={password} onChange={(e) => setPassword(e.target.value)} />
        <label className="block text-sm"><span className="mb-1 block font-medium">Role</span>
          <RoleSelect label="Role" value={role} roles={PERSON_ROLES} onChange={setRole} /></label>
      </div>
      <Muted>{ROLE[role].text}</Muted>
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
  if (!open) return <Button variant="secondary" className="mt-3" onClick={() => setOpen(true)}>Add an app</Button>;
  return (
    <form className="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800"
      onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" placeholder="e.g. homepage" autoComplete="off" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="block text-sm"><span className="mb-1 block font-medium">Role</span>
          <RoleSelect label="Role" value={role} roles={APP_ROLES} onChange={setRole} /></label>
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
