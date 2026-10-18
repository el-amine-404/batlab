import { ChevronDown, Loader2, Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type OptionsPatch, type VideoOptions } from "@/lib/api";
import { useDestinations, useJobsChanged, useSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { OptionsFields } from "./OptionsFields";

// The destination select's two values that aren't a destination.
const DEFAULT = "__default";
const HELD = "__held";

/** A link to download, where it goes, and, when asked, how. */
export function NewDownload() {
  const navigate = useNavigate();
  const changed = useJobsChanged();
  const { data: settings } = useSettings();
  const { data: destinations } = useDestinations();
  const [url, setUrl] = useState("");
  const [destination, setDestination] = useState(DEFAULT);
  const [options, setOptions] = useState<OptionsPatch>({});
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);

  const list = destinations?.destinations ?? [];
  const fallback = list.find((d) => d.slug === settings?.default_destination);
  const chosen = destination === DEFAULT ? fallback : list.find((d) => d.slug === destination);
  // What the options left on "Default" come to: the destination's, then the settings'.
  const inherited = settings ? ({ ...settings.defaults, ...dropUnset(chosen?.options ?? {}) } as VideoOptions) : undefined;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const job = await api.createJob({
        url: url.trim(),
        destination: destination === DEFAULT ? undefined : destination === HELD ? "" : destination,
        options,
        priority: 0,
        client: "videodrop page",
      });
      const fresh = job.created_at > Date.now() / 1000 - 10;
      toast.success(fresh ? "Queued" : "Asked for already: that one goes on", {
        action: { label: "Open", onClick: () => navigate(`/jobs/${job.id}`) },
      });
      setUrl("");
      changed();
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't ask for it."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="mb-6 p-4 sm:p-5">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <Label htmlFor="new-url">Link</Label>
            <Input id="new-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false}
                   placeholder="https://www.youtube.com/watch?v=…" />
          </div>
          <div className="flex flex-col gap-1.5 sm:w-56">
            <Label htmlFor="new-destination">Into</Label>
            <Select value={destination} onValueChange={setDestination}>
              <SelectTrigger id="new-destination"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT}>Default{fallback ? ` (${fallback.name})` : " (kept here)"}</SelectItem>
                {list.map((d) => <SelectItem key={d.slug} value={d.slug}>{d.name}</SelectItem>)}
                <SelectItem value={HELD}>Kept here, to download</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={busy || !url.trim()} className="sm:w-36">
            {busy ? <Loader2 className="animate-spin" /> : <Plus />} Download
          </Button>
        </div>
        <button type="button" onClick={() => setMore(!more)} aria-expanded={more}
                className="flex items-center gap-1.5 self-start text-xs text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("size-3.5 transition-transform", more && "rotate-180")} />
          {more ? "Fewer options" : "Quality, file and subtitles"}
          {!more && Object.values(options).some((v) => v !== undefined) && <span className="text-sky-300">· changed</span>}
        </button>
        {more && inherited && <OptionsFields id="new" value={options} inherited={inherited} onChange={setOptions} />}
      </form>
    </Card>
  );
}

function dropUnset<T extends object>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== null && v !== undefined)) as Partial<T>;
}
