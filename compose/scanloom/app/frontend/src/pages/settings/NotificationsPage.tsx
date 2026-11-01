import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCircle2, CircleDashed, Clock, Loader2, Send, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed, Loading } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type NotificationRow, type NotifySettings } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

const PREVIEWS: { id: NotifySettings["previews"]; label: string; hint: string }[] = [
  { id: "safe", label: "Safe", hint: "Small and blurred: you can tell what it is, not read it." },
  { id: "readable", label: "Readable", hint: "Clear thumbnails, sent to wherever the channel is." },
  { id: "off", label: "None", hint: "Words only." },
];

function StatusIcon({ row }: { row: NotificationRow }) {
  if (row.status === "sent") return <CheckCircle2 className="size-4 shrink-0 text-success" aria-label="Sent" />;
  if (row.status === "failed") return <TriangleAlert className="size-4 shrink-0 text-destructive" aria-label="Failed" />;
  if (row.status === "queued") return <Clock className="size-4 shrink-0 text-muted-foreground" aria-label="Waiting" />;
  return <CircleDashed className="size-4 shrink-0 text-muted-foreground" aria-label="Not sent" />;
}

export default function NotificationsPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 15_000 });
  const [draft, setDraft] = useState<NotifySettings | null>(null);
  useEffect(() => { if (query.data && !draft) setDraft(query.data.settings); }, [query.data, draft]);
  const save = useMutation({
    mutationFn: () => api.saveNotifications(draft!),
    onSuccess: (saved) => { client.setQueryData(["notifications"], saved); setDraft(saved.settings); toast.success("Saved"); },
    onError: (error) => toast.error(errorText(error)),
  });
  const test = useMutation({
    mutationFn: async () => { await api.saveNotifications(draft!); return api.testNotification(); },
    onSuccess: (answer) => {
      void client.invalidateQueries({ queryKey: ["notifications"] });
      if (answer.sent) toast.success("Sent: check the channel");
      else toast.error(answer.error ?? "It wasn't sent");
    },
    onError: (error) => toast.error(errorText(error)),
  });
  if (query.error) return <Failed message={errorText(query.error)} />;
  if (!draft || !query.data) return <Loading />;
  const set = (change: Partial<NotifySettings>) => setDraft({ ...draft, ...change });
  const row = (id: string, title: string, hint: string, checked: boolean, onChange: (value: boolean) => void) => (
    <div className="flex items-center justify-between gap-4">
      <div><Label htmlFor={id}>{title}</Label><p className="text-xs text-muted-foreground">{hint}</p></div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
  return (
    <>
      <PageHeader title="Notifications" description="What the admin is told, through Apprise. The same settings are in the API: /api/v2/notifications." />
      <Card className="flex flex-col gap-5 p-5">
        {row("enabled", "Send notifications", "Off: nothing goes out.", draft.enabled, (enabled) => set({ enabled }))}
        <div className={cn("flex flex-col gap-5", !draft.enabled && "opacity-50")}>
          <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="url">Apprise address</Label>
              <Input id="url" value={draft.url} placeholder="http://apprise:8000/notify/scanloom" onChange={(event) => set({ url: event.target.value })} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="tag">Channel (tag)</Label>
              <Input id="tag" value={draft.tag} placeholder="requests" onChange={(event) => set({ tag: event.target.value })} />
            </div>
          </div>
          {row("requested", "When a scan is asked for", "Who, what scan, how many photos.", draft.on_requested, (on_requested) => set({ on_requested }))}
          {row("finished", "When a scan is finished", "How many pages, what to review, what failed and why.", draft.on_finished, (on_finished) => set({ on_finished }))}
          <div>
            <p className="flex items-center gap-1.5 text-sm font-medium"><ShieldCheck className="size-4 text-primary" /> Previews of the pages</p>
            <p className="mb-2 text-xs text-muted-foreground">Up to four. Your channel may be outside your home (Discord is), and scans are often IDs.</p>
            <div role="radiogroup" aria-label="Previews" className="grid gap-2 sm:grid-cols-3">
              {PREVIEWS.map(({ id, label, hint }) => (
                <button key={id} type="button" role="radio" aria-checked={draft.previews === id} onClick={() => set({ previews: id })}
                        className={cn("rounded-lg border p-3 text-left outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring", draft.previews === id ? "border-primary bg-primary/10" : "border-border hover:bg-accent")}>
                  <span className="block text-sm font-medium">{label}</span><span className="block text-xs text-muted-foreground">{hint}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" disabled={test.isPending || !draft.url} onClick={() => test.mutate()}>{test.isPending ? <Loader2 className="animate-spin" /> : <Send />} Save and send a test</Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </div>
      </Card>
      <h2 className="mt-8 mb-3 text-base font-semibold">Latest</h2>
      {!query.data.recent.length ? <Empty icon={Bell} title="Nothing sent yet" /> : (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {query.data.recent.map((item) => (
            <li key={item.id} className="flex items-start gap-3 px-3 py-2.5 text-sm">
              <StatusIcon row={item} />
              <div className="min-w-0 flex-1">
                <p className="truncate">{item.title ?? (item.kind === "test" ? "Test" : item.kind === "requested" ? "Scan asked for" : "Scan finished")}{item.scan ? <span className="text-muted-foreground"> · {item.scan}</span> : null}</p>
                {item.error && <p className="text-xs text-destructive">{item.error}</p>}
                {item.status === "skipped" && !item.error && <p className="text-xs text-muted-foreground">Not sent: turned off</p>}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(item.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
