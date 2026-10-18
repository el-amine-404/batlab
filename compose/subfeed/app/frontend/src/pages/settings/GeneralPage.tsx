import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText, type Settings, type SettingsPatch } from "@/lib/api";
import { keys, useSettings } from "@/lib/queries";

type Numeric = "default_poll_minutes" | "keep_days" | "failing_after" | "backfill_posts";

const NUMBERS: { key: Numeric; label: string; help: string; min: number; max: number }[] = [
  { key: "default_poll_minutes", label: "Read a new feed every (minutes)", min: 15, max: 1440,
    help: "Unless the feed says otherwise. Reddit limits how often one address asks: every feed shares it." },
  { key: "backfill_posts", label: "History a new feed takes (posts)", min: 0, max: 1000,
    help: "From PullPush, Reddit's archive, a few days behind. 0 starts a feed with what is new." },
  { key: "keep_days", label: "Keep posts for (days)", min: 0, max: 3650,
    help: "Older posts are forgotten, and leave the feeds. 0 keeps them all." },
  { key: "failing_after", label: "A feed fails after (reads in a row)", min: 1, max: 50,
    help: "Then it is said to be failing (the notification), once, until it works again." },
];

export default function GeneralPage() {
  const client = useQueryClient();
  const { data, error, isPending, refetch } = useSettings();
  const [form, setForm] = useState<Record<keyof Settings, string>>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) setForm(Object.fromEntries(Object.entries(data).map(([k, v]) => [k, String(v)])) as Record<keyof Settings, string>);
  }, [data]);

  if (isPending || (!error && !form)) return <Loading />;
  if (error || !form || !data) return <Failed error={error} retry={() => void refetch()} />;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    // Only what changed: the rest keeps its value, even if another tab changed it meanwhile.
    const patch: SettingsPatch = {};
    for (const { key } of NUMBERS) if (Number(form[key]) !== data[key]) patch[key] = Number(form[key]);
    if (form.public_url.trim() !== data.public_url) patch.public_url = form.public_url.trim();
    if (!Object.keys(patch).length) return toast.info("Nothing changed.");
    setBusy(true);
    try {
      client.setQueryData(keys.settings, await api.saveSettings(patch));
      toast.success("Saved");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't save the settings."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader eyebrow="Settings" title="General" description="How subfeed reads the feeds, and what it keeps." />
      <form onSubmit={save}>
        <Card className="flex flex-col gap-5 p-5">
          {NUMBERS.map(({ key, label, help, min, max }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={key}>{label}</Label>
              <Input id={key} type="number" min={min} max={max} required value={form[key]} className="max-w-40"
                     onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              <p className="text-xs text-muted-foreground">{help}</p>
            </div>
          ))}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="public_url">The page's address</Label>
            <Input id="public_url" type="url" value={form.public_url} placeholder="http://subfeed.homelab.lan"
                   onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
            <p className="text-xs text-muted-foreground">For links in messages. Empty: messages carry none.</p>
          </div>
          <Button type="submit" className="self-start" disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save</Button>
        </Card>
      </form>
    </>
  );
}
