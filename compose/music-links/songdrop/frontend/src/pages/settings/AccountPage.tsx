import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Loader2, LogOut, MonitorSmartphone, Share2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/PageHeader";
import { Failed, Loading } from "@/components/layout/States";
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

/** A bookmark that sends the page it is clicked on to songdrop: href set by hand, React refuses javascript: links. */
function Bookmarklet() {
  const link = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    const target = `${window.location.origin}/?url=`;
    link.current?.setAttribute("href", `javascript:void(window.open(${JSON.stringify(target)}+encodeURIComponent(location.href)))`);
  }, []);
  return (
    <a ref={link} onClick={(event) => event.preventDefault()} draggable
       className="inline-flex cursor-grab items-center gap-2 rounded-lg border border-violet-400/40 bg-violet-400/10 px-3 py-1.5 text-sm font-medium text-violet-200 active:cursor-grabbing">
      <Bookmark className="size-4" /> Send to songdrop
    </a>
  );
}

export default function AccountPage() {
  const client = useQueryClient();
  const labels = usePermissionLabels();
  const { data, error, isPending } = useQuery({ queryKey: ACCOUNT, queryFn: api.account });
  const secure = window.isSecureContext;

  const elsewhere = async () => {
    try {
      client.setQueryData(ACCOUNT, await api.signOutElsewhere());
      toast.success("Signed out everywhere else");
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed message={errorText(error)} />;
  const others = data.sessions.filter((session) => !session.current).length;

  return (
    <>
      <PageHeader title="Your account" description="Who you are here, your password, and where you're signed in."
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

      <Section title="Send links to songdrop" description="Ask for a song, an album or a playlist from the page you're on, without copying its link here.">
        <div className="flex flex-col gap-4 text-sm">
          <div className="flex flex-col gap-2">
            <p className="font-medium">On a computer</p>
            <p className="text-muted-foreground">Drag this to your bookmarks bar; on a song's page (Spotify, YouTube…), click it: songdrop opens with the link filled in.</p>
            <div><Bookmarklet /></div>
          </div>
          <div className="flex flex-col gap-2">
            <p className="flex items-center gap-2 font-medium"><Share2 className="size-4" /> On a phone</p>
            {secure ? (
              <p className="text-muted-foreground">
                Install songdrop (the browser's menu: "Add to Home screen" or "Install"); then it is in the Share menu of YouTube and the other apps.
              </p>
            ) : (
              <p className="text-muted-foreground">
                Phones offer an installed site in their Share menu only over HTTPS, and songdrop is reached over plain HTTP here. Until it has
                HTTPS, share the link to your browser and open <code className="text-foreground">{window.location.origin}/?url=</code> followed by it,
                or paste it on the Add page.
              </p>
            )}
          </div>
        </div>
      </Section>
    </>
  );
}
