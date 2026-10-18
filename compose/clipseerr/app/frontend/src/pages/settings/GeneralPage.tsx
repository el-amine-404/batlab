import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type SettingsPatch } from "@/lib/api";
import { keys, useSettings } from "@/lib/queries";

function Row({ label, help, children, htmlFor }: { label: string; help: string; children: React.ReactNode; htmlFor: string }) {
  return (
    <div className="flex flex-col gap-3 border-b border-border px-5 py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <Label htmlFor={htmlFor}>{label}</Label>
        <p className="mt-0.5 text-xs text-muted-foreground">{help}</p>
      </div>
      <div className="w-full shrink-0 sm:w-64">{children}</div>
    </div>
  );
}

export default function GeneralPage() {
  const client = useQueryClient();
  const { data, error, refetch, isPending } = useSettings();
  const [form, setForm] = useState<SettingsPatch>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data) setForm({ region: data.region, storefront: data.storefront, poll_seconds: data.poll_seconds,
                        attempt_timeout_hours: data.attempt_timeout_hours, hide_explicit: data.hide_explicit,
                        hot_queries: data.hot_queries, hot_feeds: data.hot_feeds });
  }, [data]);

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;

  const save = async () => {
    setSaving(true);
    try {
      const lines = (list?: string[] | null) => list?.map((line) => line.trim()).filter(Boolean);
      const saved = await api.saveSettings({ ...form, hot_queries: lines(form.hot_queries), hot_feeds: lines(form.hot_feeds) });
      client.setQueryData(keys.settings, saved);
      if (saved.region !== data.region || saved.storefront !== data.storefront || saved.hide_explicit !== data.hide_explicit
          || saved.hot_queries.join("\n") !== data.hot_queries.join("\n") || saved.hot_feeds.join("\n") !== data.hot_feeds.join("\n")) {
        // Other charts: every list is read again.
        await client.invalidateQueries({ predicate: (q) => !["settings", "services", "notifications"].includes(String(q.queryKey[0])) });
      }
      toast.success("Saved");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't save."));
    } finally {
      setSaving(false);
    }
  };

  const set = <K extends keyof SettingsPatch>(key: K, value: SettingsPatch[K]) => setForm((old) => ({ ...old, [key]: value }));

  return (
    <>
      <PageHeader eyebrow="Settings" title="General" actions={<Button onClick={save} disabled={saving}>{saving && <Loader2 className="animate-spin" />} Save</Button>} />
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">What to browse</h2>
      <Card className="mb-8">
        <Row htmlFor="region" label="YouTube country" help="The new videos, Trending and the charts on Discover; genre charts exist for a few countries.">
          <Select value={form.region ?? undefined} onValueChange={(v) => set("region", v)}>
            <SelectTrigger id="region"><SelectValue /></SelectTrigger>
            <SelectContent>{data.regions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </Row>
        <Row htmlFor="storefront" label="Apple Music store" help="Just released, Top on Apple Music, and the Apple genres.">
          <Select value={form.storefront ?? undefined} onValueChange={(v) => set("storefront", v)}>
            <SelectTrigger id="storefront"><SelectValue /></SelectTrigger>
            <SelectContent>{data.storefronts.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </Row>
        <Row htmlFor="explicit" label="Hide explicit videos" help="Leaves videos marked explicit out of every list.">
          <div className="flex sm:justify-end"><Switch id="explicit" checked={!!form.hide_explicit} onCheckedChange={(v) => set("hide_explicit", v)} /></div>
        </Row>
        <div className="flex flex-col gap-3 px-5 py-4">
          <div>
            <Label htmlFor="hot">The Hot category</Label>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Newest first, from two kinds of source. Feeds: one address a line (8 at most), any JSON Feed,
              such as a subreddit's from subfeed (subfeed.homelab.lan, "Read it elsewhere"); clipseerr keeps
              the videos that are real songs. Searches: one a line (12 at most), the official videos YouTube finds.
            </p>
          </div>
          <Label htmlFor="hot-feeds" className="text-xs text-muted-foreground">Feeds</Label>
          <textarea id="hot-feeds" rows={2} value={(form.hot_feeds ?? []).join("\n")} spellCheck={false}
                    placeholder="http://subfeed:8830/api/v1/feeds/sexymusicvideos/feed.json?video=youtube&unique=true&limit=500"
                    onChange={(e) => set("hot_feeds", e.target.value.split("\n").slice(0, 8))}
                    className="w-full resize-y rounded-lg border border-input bg-black/20 px-3 py-2 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40" />
          <Label htmlFor="hot" className="text-xs text-muted-foreground">YouTube searches</Label>
          <textarea id="hot" rows={6} value={(form.hot_queries ?? []).join("\n")}
                    onChange={(e) => set("hot_queries", e.target.value.split("\n").slice(0, 12))}
                    className="w-full resize-y rounded-lg border border-input bg-black/20 px-3 py-2 font-mono text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40" />
        </div>
      </Card>
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">Following requests</h2>
      <Card>
        <Row htmlFor="poll" label="Ask the services every" help="Seconds between two looks at the requests under way (5 to 600).">
          <Input id="poll" type="number" min={5} max={600} value={form.poll_seconds ?? ""} onChange={(e) => set("poll_seconds", Number(e.target.value))} />
        </Row>
        <Row htmlFor="timeout" label="Give a service up after" help="Hours a service may take before the next one gets the request (1 to 168).">
          <Input id="timeout" type="number" min={1} max={168} value={form.attempt_timeout_hours ?? ""} onChange={(e) => set("attempt_timeout_hours", Number(e.target.value))} />
        </Row>
      </Card>
    </>
  );
}
