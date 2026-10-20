import { AlertCircle, ArrowDownToLine, ChevronDown, Link2, Loader2, SlidersHorizontal, X } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, type Detection, type Engine, errorText, type OptionField, type OptionValues } from "@/lib/api";
import { useDetect, useOptionsSpec } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { engineVariant, kindLabel } from "./labels";
import { OptionsPanel } from "./OptionsPanel";

/** A link songdrop can download: the engine, site and kind are known. */
type Supported = Detection & { supported: true; engine: Engine; site: string; kind: string };

const supported = (detection: Detection | null): Supported | null =>
  detection?.supported && detection.engine && detection.site && detection.kind ? (detection as Supported) : null;

function defaults(fields: OptionField[]): OptionValues {
  return Object.fromEntries(fields.map((field) => [field.key, field.default]));
}

function visible(fields: OptionField[], detection: Supported): OptionField[] {
  return fields.filter(
    (field) =>
      (!field.kinds || field.kinds.includes(detection.kind)) &&
      (field.needs !== "has_playlist" || detection.has_playlist) &&
      (field.needs !== "list" || detection.kind === "playlist" || detection.has_playlist),
  );
}

function DetectionLine({ url, detection, checking }: { url: string; detection: Detection | null; checking: boolean }) {
  if (!url.trim()) {
    return <span className="text-xs text-muted-foreground">Spotify, YouTube, SoundCloud, Bandcamp and the other sites yt-dlp knows.</span>;
  }
  if (checking || !detection) {
    return (
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Checking the link…
      </span>
    );
  }
  const ready = supported(detection);
  if (!ready) {
    return (
      <span className="flex items-start gap-1.5 text-xs text-destructive">
        <AlertCircle className="mt-px size-3.5 shrink-0" /> {detection.message ?? "songdrop can't download this link."}
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <Badge variant={engineVariant(ready.engine, ready.site)}>
        <span className="size-1.5 rounded-full bg-current" /> {ready.site} {kindLabel(ready.kind)}
      </Badge>
      via {ready.engine_label}
    </span>
  );
}

export function LinkForm({ onCreated }: { onCreated: () => void }) {
  const navigate = useNavigate();
  // A link sent here (/?url=…: the bookmark, the phone's Share menu) fills the box; the address is left clean.
  const [params, setParams] = useSearchParams();
  const [url, setUrl] = useState(() => params.get("url") ?? "");
  useEffect(() => {
    if (params.has("url")) setParams({}, { replace: true });
  }, [params, setParams]);
  const [submitting, setSubmitting] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const spec = useOptionsSpec();
  const { detection, checking } = useDetect(url);
  // Changes for this download only; they start from the defaults in Settings.
  const [chosen, setChosen] = useState<Partial<Record<Engine, OptionValues>>>({});

  const ready = supported(detection);
  const engine = ready?.engine;
  const fields = useMemo(
    () => (engine && spec && ready ? visible(spec.engines[engine].fields, ready) : []),
    [engine, spec, ready],
  );
  const values: OptionValues = useMemo(() => {
    if (!engine || !spec) return {};
    // This download's choices over the defaults, ignoring keys the server no longer has.
    const base = defaults(spec.engines[engine].fields);
    const saved = chosen[engine] ?? {};
    return { ...base, ...Object.fromEntries(Object.entries(saved).filter(([key]) => key in base)) };
  }, [engine, spec, chosen]);

  const save = (next: Partial<Record<Engine, OptionValues>>) => setChosen(next);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    try {
      // Only the fields shown for this link; hidden ones keep the server's defaults.
      const sent = Object.fromEntries(fields.map((field) => [field.key, values[field.key]]));
      const job = await api.createJob(url.trim(), sent);
      toast.success("Added", {
        description: `${job.site} ${kindLabel(job.kind)} queued for download.`,
        action: { label: "Open the queue", onClick: () => navigate("/activity/queue") },
      });
      setUrl("");
      setShowOptions(false);
      setChosen({});
      onCreated();
    } catch (caught) {
      toast.error("Couldn't start the download", { description: errorText(caught) });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="p-3 sm:p-4">
      <form onSubmit={submit} noValidate>
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Link2 className="pointer-events-none absolute top-1/2 left-3.5 size-5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="Paste a Spotify or YouTube link"
              aria-label="Link"
              type="url"
              inputMode="url"
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="go"
              className="h-12 rounded-xl pr-10 pl-11"
            />
            {url && (
              <button
                type="button"
                onClick={() => setUrl("")}
                aria-label="Clear"
                className="absolute top-1/2 right-2 flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <Button type="submit" size="lg" disabled={!ready || submitting || checking} className="h-12 sm:w-36">
            {submitting ? <Loader2 className="animate-spin" /> : <ArrowDownToLine />}
            Download
          </Button>
        </div>

        <div className="mt-3 flex min-h-8 items-center justify-between gap-3 px-1">
          <DetectionLine url={url} detection={detection} checking={checking} />
          {ready && fields.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-expanded={showOptions}
              onClick={() => setShowOptions((open) => !open)}
            >
              <SlidersHorizontal /> Options
              <ChevronDown className={cn("transition-transform", showOptions && "rotate-180")} />
            </Button>
          )}
        </div>

        {ready && engine && spec && showOptions && (
          <OptionsPanel
            title={spec.engines[engine].label}
            fields={fields}
            values={values}
            onChange={(next) => save({ ...chosen, [engine]: next })}
            onReset={() => save({ ...chosen, [engine]: defaults(spec.engines[engine].fields) })}
          />
        )}
      </form>
    </Card>
  );
}
