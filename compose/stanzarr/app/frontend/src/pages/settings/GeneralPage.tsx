import { useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Settings, type SettingsPatch } from "@/lib/api";
import { CUTOFFS } from "@/lib/format";
import { keys, useSettings } from "@/lib/queries";

type Numeric = "scan_minutes" | "retry_days";

const NUMBERS: { key: Numeric; label: string; help: string; min: number; max: number }[] = [
  { key: "scan_minutes", label: "Read the library every (minutes)", min: 1, max: 1440,
    help: "For new and changed songs, and their lyrics files. A scan reads only what changed." },
  { key: "retry_days", label: "Ask the sources again after (days)", min: 1, max: 365,
    help: "For a song without lyrics synced by a person: they may have some by then." },
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

  const scanNow = async () => {
    try {
      await api.scan();
      toast.success("Scanning: new songs show in the library as they're found.");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't start a scan."));
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    // Only what changed: the rest keeps its value, even if another tab changed it meanwhile.
    const patch: SettingsPatch = {};
    for (const { key } of NUMBERS) if (Number(form[key]) !== data[key]) patch[key] = Number(form[key]);
    if (form.public_url.trim() !== data.public_url) patch.public_url = form.public_url.trim();
    if (form.cutoff !== data.cutoff) patch.cutoff = form.cutoff as Settings["cutoff"];
    if ((form.auto_ai === "true") !== data.auto_ai) patch.auto_ai = form.auto_ai === "true";
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
      <PageHeader eyebrow="Settings" title="General" description="How often stanzarr reads the library and asks the sources."
                  actions={<Button variant="secondary" onClick={() => void scanNow()}><RefreshCw /> Scan now</Button>} />
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
            <Input id="public_url" type="url" value={form.public_url} placeholder="http://stanzarr.homelab.lan"
                   onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
            <p className="text-xs text-muted-foreground">For links in messages. Empty: messages carry none.</p>
          </div>
          <fieldset className="flex flex-col gap-2 border-t border-border pt-5">
            <legend className="mb-2 text-sm font-medium">Quality: searched until</legend>
            <p className="-mt-1 text-xs text-muted-foreground">
              The ladder, worst first. A song below the cutoff is asked about again every few days; one at it or above is left alone.
              A better version is always taken, and a person's own file is never replaced.
            </p>
            {CUTOFFS.map((c) => (
              <label key={c.value} className="flex items-center gap-2 text-sm">
                <input type="radio" name="cutoff" value={c.value} checked={form.cutoff === c.value}
                       onChange={() => setForm({ ...form, cutoff: c.value })} className="accent-primary" />
                {c.rank}. {c.label}{c.value === "synced" && <span className="text-xs text-muted-foreground">(the usual)</span>}
              </label>
            ))}
          </fieldset>
          <div className="flex items-start gap-3">
            <Switch id="auto_ai" checked={form.auto_ai === "true"} onCheckedChange={(on) => setForm({ ...form, auto_ai: String(on) })} />
            <div className="flex flex-col gap-1">
              <Label htmlFor="auto_ai">Sync plain lyrics with AI by themselves</Label>
              <p className="text-xs text-muted-foreground">
                Songs with words but no timings, once the sources were asked, go to the GPU workers (20 a pass, when one is online).
                Only below the cutoff; a synced file found later replaces the AI's.
              </p>
            </div>
          </div>
          <Button type="submit" className="self-start" disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save</Button>
        </Card>
      </form>
    </>
  );
}
