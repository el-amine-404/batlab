import { AlertCircle, ArrowDownToLine, ChevronDown, Link2, Loader2, SlidersHorizontal, X } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, ApiError, type Detection, type Engine, type Field, type OptionValues } from "@/lib/api";
import { stored, useDetect, useOptionsSpec } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { engineVariant, kindLabel } from "./labels";
import { OptionsPanel } from "./OptionsPanel";

const STORAGE_KEY = "songdrop.options.v1";

function defaults(fields: Field[]): OptionValues {
  return Object.fromEntries(fields.map((field) => [field.key, field.default]));
}

function visible(fields: Field[], detection: Extract<Detection, { supported: true }>): Field[] {
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
  if (!detection.supported) {
    return (
      <span className="flex items-start gap-1.5 text-xs text-destructive">
        <AlertCircle className="mt-px size-3.5 shrink-0" /> {detection.message}
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <Badge variant={engineVariant(detection.engine, detection.site)}>
        <span className="size-1.5 rounded-full bg-current" /> {detection.site} {kindLabel(detection.kind)}
      </Badge>
      via {detection.engine_label}
    </span>
  );
}

export function LinkForm({ onCreated }: { onCreated: () => void }) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const spec = useOptionsSpec();
  const { detection, checking } = useDetect(url);
  const [chosen, setChosen] = useState<Partial<Record<Engine, OptionValues>>>(
    () => stored.get<Partial<Record<Engine, OptionValues>>>(STORAGE_KEY) ?? {},
  );

  const ready = detection?.supported ? detection : null;
  const engine = ready?.engine;
  const fields = useMemo(
    () => (engine && spec && ready ? visible(spec.engines[engine].fields, ready) : []),
    [engine, spec, ready],
  );
  const values: OptionValues = useMemo(() => {
    if (!engine || !spec) return {};
    // Saved choices over the defaults, ignoring keys the server no longer has.
    const base = defaults(spec.engines[engine].fields);
    const saved = chosen[engine] ?? {};
    return { ...base, ...Object.fromEntries(Object.entries(saved).filter(([key]) => key in base)) };
  }, [engine, spec, chosen]);

  const save = (next: Partial<Record<Engine, OptionValues>>) => {
    setChosen(next);
    stored.set(STORAGE_KEY, next);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    try {
      // Only the fields shown for this link; hidden ones keep the server's defaults.
      const sent = Object.fromEntries(fields.map((field) => [field.key, values[field.key]]));
      const job = await api.create(url.trim(), sent);
      toast.success("Added", { description: `${job.site} ${kindLabel(job.kind)} queued for download.` });
      setUrl("");
      setShowOptions(false);
      onCreated();
    } catch (caught) {
      toast.error("Couldn't start the download", {
        description: caught instanceof ApiError ? caught.message : "Something went wrong.",
      });
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
