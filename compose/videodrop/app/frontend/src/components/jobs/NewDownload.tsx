import { useQuery } from "@tanstack/react-query";
import { ChevronDown, CircleCheck, CircleX, Loader2, Plus } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, ApiError, errorText, type OptionsPatch, type Section, type VideoOptions } from "@/lib/api";
import { useDestinations, useJobsChanged, useSettings } from "@/lib/queries";
import { useDebounced } from "@/lib/useDebounced";
import { cn } from "@/lib/utils";

import { OptionsFields } from "./OptionsFields";
import { SectionPicker } from "./SectionPicker";
import { VideoPreview } from "./VideoPreview";

// The "save to" choice that isn't a destination: kept here for whoever asked.
const HELD = "__held";

function dropUnset<T extends object>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== null && v !== undefined)) as Partial<T>;
}

/**
 * A link to download, where it goes and, when asked, how. The link is
 * checked as it is typed (instantly, without asking the site); once it is a
 * link videodrop takes, the site is read for a preview: what would be
 * downloaded, at what size, and where it would land. /?url=<link> fills
 * the link in.
 */
export function NewDownload() {
  const navigate = useNavigate();
  const changed = useJobsChanged();
  const { data: settings } = useSettings();
  const { data: destinations } = useDestinations();
  // /?url=<link> opens with the link filled in (a bookmark, another app's link).
  const [params] = useSearchParams();
  const [url, setUrl] = useState(() => params.get("url") ?? "");
  const [chosen, setChosen] = useState<string | null>(null);
  const [options, setOptions] = useState<OptionsPatch>({});
  const [more, setMore] = useState(false);
  const [again, setAgain] = useState(false);
  const [section, setSection] = useState<Section | null>(null);
  const [busy, setBusy] = useState(false);

  const list = destinations?.destinations ?? [];
  // Until a choice is made: the settings' default destination, else kept here.
  const destination = chosen ?? (settings?.default_destination || HELD);
  const destinationRow = list.find((d) => d.slug === destination);
  const inherited = settings ? ({ ...settings.defaults, ...dropUnset(destinationRow?.options ?? {}) } as VideoOptions) : undefined;

  // The link, checked as typed (no request to the site).
  const typed = useDebounced(url.trim(), 250);
  const check = useQuery({
    queryKey: ["link-check", typed],
    queryFn: ({ signal }) => api.checkLink(typed, signal),
    enabled: typed.length > 0,
    staleTime: Infinity,
    retry: false,
  });
  const linkOk = !!typed && check.data?.ok === true && typed === url.trim();

  // The preview, once the link is good and the choices have settled.
  const probeKey = useDebounced({ url: check.data?.ok ? check.data.url : "", destination, options, section }, 600);
  const probe = useQuery({
    queryKey: ["probe", probeKey],
    queryFn: ({ signal }) => api.probe({ url: probeKey.url, destination: probeKey.destination === HELD ? "" : probeKey.destination,
                                         options: probeKey.options, section: probeKey.section }, signal),
    enabled: !!probeKey.url && linkOk,
    staleTime: 10 * 60 * 1000,
    retry: false,
    // Same video, other options: the last preview stays while the new one loads.
    placeholderData: (previous, previousQuery) =>
      (previousQuery?.queryKey[1] as typeof probeKey | undefined)?.url === probeKey.url ? previous : undefined,
  });
  const probeError = probe.error instanceof ApiError ? probe.error.message : probe.error ? "The site couldn't be read." : undefined;

  useEffect(() => {
    if (!url) setMore(false);
  }, [url]);
  // Another video: its own part, if any.
  useEffect(() => setSection(null), [check.data?.url]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!linkOk) return;
    setBusy(true);
    try {
      const job = await api.createJob({
        url: url.trim(),
        destination: destination === HELD ? "" : destination,
        options,
        priority: 0,
        section,
        again,
        client: "videodrop page",
      });
      const fresh = job.created_at > Date.now() / 1000 - 10;
      toast.success(!fresh ? "Asked for already: that one goes on" : probe.data?.target?.earlier && !again ? "Queued: the earlier file will be reused" : "Queued", {
        action: { label: "Open", onClick: () => navigate(`/jobs/${job.id}`) },
      });
      setUrl("");
      setOptions({});
      setAgain(false);
      setSection(null);
      changed();
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't ask for it."));
    } finally {
      setBusy(false);
    }
  };

  const status = !url.trim() ? null
    : typed !== url.trim() || check.isFetching ? { icon: <Loader2 className="size-3.5 animate-spin" />, text: "Checking the link…", tone: "text-muted-foreground" }
    : check.data?.ok ? { icon: <CircleCheck className="size-3.5" />, text: `A link videodrop takes · ${check.data.site}`, tone: "text-success" }
    : check.data ? { icon: <CircleX className="size-3.5" />, text: check.data.message ?? "Not a link videodrop takes.", tone: "text-destructive" }
    : null;
  const optionsChanged = Object.values(options).some((v) => v !== undefined);

  return (
    <Card className="mb-6 p-4 sm:p-5">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <Label htmlFor="new-url">Link</Label>
            <Input id="new-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false}
                   placeholder="https://www.youtube.com/watch?v=…" aria-invalid={check.data?.ok === false || undefined}
                   aria-describedby="new-url-status" className={cn(check.data?.ok === false && typed === url.trim() && "border-destructive/60")} />
            <p id="new-url-status" className={cn("flex min-h-4 items-center gap-1.5 text-xs", status?.tone)} aria-live="polite">
              {status?.icon} {status?.text}
            </p>
          </div>
          <div className="flex flex-col gap-1.5 sm:w-64">
            <Label htmlFor="new-destination">Save to</Label>
            <Select value={destination} onValueChange={setChosen}>
              <SelectTrigger id="new-destination"><SelectValue /></SelectTrigger>
              <SelectContent>
                {list.map((d) => (
                  <SelectItem key={d.slug} value={d.slug}>
                    {d.name} <span className="text-xs text-muted-foreground">· media/{d.folder}</span>
                  </SelectItem>
                ))}
                <SelectItem value={HELD}>Nowhere: keep it here to download</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {destination === HELD ? `Kept ${settings?.hold_hours ?? 24} h; download it from this page.` : "Into the library folder; Jellyfin sees it."}
            </p>
          </div>
          <Button type="submit" disabled={busy || !linkOk} className="sm:mt-6 sm:w-36">
            {busy ? <Loader2 className="animate-spin" /> : <Plus />} Download
          </Button>
        </div>

        {linkOk && <VideoPreview probe={probe.data} loading={probe.isFetching || (!probe.data && !probeError)} error={probeError} holdHours={settings?.hold_hours}
                                  again={again} onAgain={setAgain} />}
        {linkOk && probe.data?.kind === "video" && !!probe.data.duration && !probe.data.is_live && !inherited?.playlist && (
          <SectionPicker duration={probe.data.duration} value={section} onChange={setSection} />
        )}

        <button type="button" onClick={() => setMore(!more)} aria-expanded={more}
                className="flex items-center gap-1.5 self-start text-xs text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("size-3.5 transition-transform", more && "rotate-180")} />
          {more ? "Hide the options" : "Quality, subtitles, lyrics…"}
          {!more && optionsChanged && <span className="text-sky-300">· changed</span>}
        </button>
        {more && inherited && (
          <div className="rounded-xl border border-border p-4">
            <OptionsFields id="new" value={options} inherited={inherited} onChange={setOptions}
                           available={probe.data?.kind === "video" ? { subtitles: probe.data.subtitles ?? [], automatic: probe.data.automatic_captions ?? 0, automaticEnglish: !!probe.data.automatic_has_english } : undefined} />
            {optionsChanged && (
              <button type="button" className="mt-4 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline" onClick={() => setOptions({})}>
                Back to the {destinationRow ? `${destinationRow.name}'s` : "default"} options
              </button>
            )}
          </div>
        )}
      </form>
    </Card>
  );
}
