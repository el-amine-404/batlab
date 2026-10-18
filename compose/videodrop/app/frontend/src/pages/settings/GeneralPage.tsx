import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { OptionsFields } from "@/components/jobs/OptionsFields";
import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type OptionsPatch, type Settings, type SettingsPatch } from "@/lib/api";
import { keys, useDestinations, useSettings } from "@/lib/queries";

type Numeric = "max_parallel" | "per_site_parallel" | "retries" | "rate_limit_mib" | "max_filesize_gb" | "min_free_gb" | "hold_hours" | "keep_days";

const NUMBERS: { key: Numeric; label: string; help: string; min: number; max: number; step?: number }[] = [
  { key: "max_parallel", label: "Downloads at once", min: 1, max: 16,
    help: "Every site together. Each one is a process of its own; more use more memory and bandwidth." },
  { key: "per_site_parallel", label: "Downloads at once from one site", min: 1, max: 16,
    help: "Every app here shares one address. YouTube asks an address that downloads too much to sign in." },
  { key: "retries", label: "Tries again after a failure", min: 0, max: 10,
    help: "Only for failures that may pass (the network, the site busy): after 1 min, 5 min, 25 min…" },
  { key: "rate_limit_mib", label: "Speed limit per download (MiB/s)", min: 0, max: 10000, step: 0.5, help: "0: as fast as it goes." },
  { key: "max_filesize_gb", label: "Largest video (GB)", min: 0, max: 1000, step: 0.5, help: "Bigger ones aren't downloaded. 0: no limit." },
  { key: "min_free_gb", label: "Keep free on the disk (GB)", min: 0, max: 10000, help: "Downloads wait while there is less; you're told once." },
  { key: "hold_hours", label: "Keep held files for (hours)", min: 1, max: 720,
    help: "Files of downloads without a destination, for their caller to fetch." },
  { key: "keep_days", label: "Remember ended downloads for (days)", min: 0, max: 3650,
    help: "Then they leave this list; their files stay. 0: forever." },
];

const NONE = "__none";

export default function GeneralPage() {
  const client = useQueryClient();
  const { data, error, isPending, refetch } = useSettings();
  const { data: destinations } = useDestinations();
  const [form, setForm] = useState<Record<Numeric | "public_url" | "default_destination", string>>();
  const [defaults, setDefaults] = useState<OptionsPatch>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!data) return;
    setForm({ ...Object.fromEntries(NUMBERS.map(({ key }) => [key, String(data[key])])),
              public_url: data.public_url, default_destination: data.default_destination || NONE } as typeof form & object);
    setDefaults({});
  }, [data]);

  if (isPending || (!error && !form)) return <Loading />;
  if (error || !form || !data) return <Failed error={error} retry={() => void refetch()} />;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    // Only what changed: the rest keeps its value, even if another tab changed it meanwhile.
    const patch: SettingsPatch = {};
    for (const { key } of NUMBERS) if (Number(form[key]) !== data[key]) patch[key] = Number(form[key]);
    if (form.public_url.trim() !== data.public_url) patch.public_url = form.public_url.trim();
    const destination = form.default_destination === NONE ? "" : form.default_destination;
    if (destination !== data.default_destination) patch.default_destination = destination;
    if (Object.values(defaults).some((v) => v !== undefined)) patch.defaults = defaults;
    if (!Object.keys(patch).length) return toast.info("Nothing changed.");
    setBusy(true);
    try {
      client.setQueryData<Settings>(keys.settings, await api.saveSettings(patch));
      toast.success("Saved");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't save the settings."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader eyebrow="Settings" title="General" description="How many downloads run, what they take, and what is kept." />
      <form onSubmit={save} className="flex flex-col gap-4">
        <Card className="grid gap-5 p-5 sm:grid-cols-2">
          {NUMBERS.map(({ key, label, help, min, max, step }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={key}>{label}</Label>
              <Input id={key} type="number" min={min} max={max} step={step ?? 1} required value={form[key]} className="max-w-40"
                     onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              <p className="text-xs text-muted-foreground">{help}</p>
            </div>
          ))}
        </Card>
        <Card className="flex flex-col gap-5 p-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="default_destination">Default destination</Label>
            <Select value={form.default_destination} onValueChange={(value) => setForm({ ...form, default_destination: value })}>
              <SelectTrigger id="default_destination" className="max-w-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>None: kept here for whoever asked</SelectItem>
                {(destinations?.destinations ?? []).map((d) => <SelectItem key={d.slug} value={d.slug}>{d.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">Where a download that names no destination goes.</p>
          </div>
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium">Default options</p>
            <p className="text-xs text-muted-foreground">What a download takes when neither it nor its destination says.</p>
            <OptionsFields id="defaults" value={defaults} inherited={data.defaults} onChange={setDefaults} inheritLabel="As now" key={JSON.stringify(data.defaults)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="public_url">The page's address</Label>
            <Input id="public_url" type="url" value={form.public_url} placeholder="http://videodrop.homelab.lan"
                   onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
            <p className="text-xs text-muted-foreground">For links in messages. Empty: messages carry none.</p>
          </div>
        </Card>
        <Button type="submit" className="self-start" disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save</Button>
      </form>
    </>
  );
}
