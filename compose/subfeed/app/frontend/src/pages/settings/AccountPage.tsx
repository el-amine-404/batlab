import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, LogOut, MonitorSmartphone } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { usePermissionLabels } from "@/components/people/PermissionPicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText } from "@/lib/api";
import { signOut } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

const ACCOUNT = ["account"] as const;

/** "Firefox on Linux" from a browser's User-Agent, roughly. */
function device(agent: string | null | undefined): string {
  if (!agent) return "A browser";
  const browser = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome"
    : /Safari\//.test(agent) ? "Safari" : "A browser";
  const system = /Android/.test(agent) ? "Android" : /iPhone|iPad/.test(agent) ? "iOS" : /Windows/.test(agent) ? "Windows"
    : /Mac OS X/.test(agent) ? "macOS" : /Linux/.test(agent) ? "Linux" : "";
  return system ? `${browser} on ${system}` : browser;
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card className="mb-4 gap-0 p-4 sm:p-5">
      <h2 className="font-semibold tracking-tight">{title}</h2>
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  );
}

function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  const mismatch = again.length > 0 && again !== next;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.changePassword(current, next);
      toast.success("Password changed; your other sessions were signed out");
      setCurrent("");
      setNext("");
      setAgain("");
      void client.invalidateQueries({ queryKey: ACCOUNT });
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-3 sm:items-end">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pw-current">Current password</Label>
        <Input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pw-new">New password</Label>
        <Input id="pw-new" type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pw-again">Again</Label>
        <Input id="pw-again" type="password" autoComplete="new-password" required aria-invalid={mismatch} value={again} onChange={(e) => setAgain(e.target.value)} />
      </div>
      <p className="text-xs text-muted-foreground sm:col-span-2">{mismatch ? <span className="text-destructive">The two differ.</span> : "At least 10 characters."}</p>
      <Button type="submit" disabled={busy || !current || next.length < 10 || mismatch || !again}>
        {busy && <Loader2 className="animate-spin" />} Change it
      </Button>
    </form>
  );
}

export default function AccountPage() {
  const client = useQueryClient();
  const labels = usePermissionLabels();
  const { data, error, isPending, refetch } = useQuery({ queryKey: ACCOUNT, queryFn: api.account });

  const elsewhere = async () => {
    try {
      client.setQueryData(ACCOUNT, await api.signOutElsewhere());
      toast.success("Signed out everywhere else");
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const others = data.sessions.filter((session) => !session.current).length;

  return (
    <>
      <PageHeader eyebrow="Settings" title="Your account" description="Who you are here, your password, and where you're signed in."
                  actions={<Button variant="secondary" onClick={() => void signOut()}><LogOut /> Sign out</Button>} />

      <Section title={data.me.name}>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          <dt className="text-muted-foreground">User name</dt><dd>{data.username}</dd>
          <dt className="text-muted-foreground">May</dt><dd>{labels(data.me.permissions)}</dd>
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">Your name and what you may do are an admin's to change.</p>
      </Section>

      <Section title="Password" description="Your other sessions end when it changes.">
        <PasswordForm />
      </Section>

      <Section title="Where you're signed in" description="A session lasts 30 days unused.">
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {data.sessions.map((session) => (
            <li key={session.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <MonitorSmartphone className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{device(session.agent)} {session.current && <Badge variant="muted" className="ml-1">this one</Badge>}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  <span title={session.address ?? undefined}>{session.host ?? session.address}</span> · {timeAgo(session.last_seen_at)}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <Button variant="secondary" className="mt-3" disabled={!others} onClick={() => void elsewhere()}>
          Sign out everywhere else{others ? ` (${others})` : ""}
        </Button>
      </Section>

    </>
  );
}
