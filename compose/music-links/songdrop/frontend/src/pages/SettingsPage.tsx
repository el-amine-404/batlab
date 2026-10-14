import { Check, FolderOpen, Loader2, Plus, X } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { CookiesCard } from "@/components/CookiesCard";
import { OptionsPanel } from "@/components/OptionsPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, ApiError, type Engine, type OptionValues, type Settings, type SettingsPatch } from "@/lib/api";
import { useOptionsSpec } from "@/lib/hooks";
import { cn } from "@/lib/utils";

type Draft = Omit<Settings, "output_root" | "output_folder_exists" | "discord_webhook_set" | "limits"> & {
  discord_webhook?: string;
};

const EDITABLE: (keyof Draft)[] = [
  "deliver", "output_folder", "retention_hours", "max_items", "concurrent_jobs", "spotdl_parallel",
  "job_timeout_minutes", "retry_minutes", "public_url", "defaults", "discord_webhook",
];

function draftOf(settings: Settings): Draft {
  const { output_root: _r, output_folder_exists: _e, discord_webhook_set: _w, limits: _l, ...rest } = settings;
  return structuredClone(rest);
}

function changes(saved: Settings, draft: Draft): SettingsPatch {
  const base = draftOf(saved) as Record<string, unknown>;
  const patch: Record<string, unknown> = {};
  for (const key of EDITABLE) {
    const value = (draft as Record<string, unknown>)[key];
    if (key === "discord_webhook") {
      if (value !== undefined) patch[key] = value;
    } else if (JSON.stringify(value) !== JSON.stringify(base[key])) {
      patch[key] = value;
    }
  }
  return patch as SettingsPatch;
}

export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${+(minutes / 60).toFixed(1)} h`;
  return `${+(minutes / 1440).toFixed(1)} d`;
}

export function SettingsPage() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.settings().then(
      (settings) => {
        setSaved(settings);
        setDraft(draftOf(settings));
      },
      (caught) => setError(caught instanceof ApiError ? caught.message : "Can't load the settings."),
    );
  }, []);

  const patch = useMemo(() => (saved && draft ? changes(saved, draft) : {}), [saved, draft]);
  const dirty = Object.keys(patch).length > 0;

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => current && { ...current, [key]: value });

  const save = async () => {
    setSaving(true);
    try {
      const next = await api.saveSettings(patch);
      setSaved(next);
      setDraft(draftOf(next));
      toast.success("Settings saved", { description: "They apply to the next download." });
    } catch (caught) {
      toast.error("Couldn't save", { description: caught instanceof ApiError ? caught.message : undefined });
    } finally {
      setSaving(false);
    }
  };

  if (error) return <Card className="p-4 text-sm text-destructive">{error}</Card>;
  if (!saved || !draft) {
    return (
      <div className="flex justify-center py-16 text-muted-foreground">
        <Loader2 className="animate-spin" />
      </div>
    );
  }
  const limit = (key: string) => saved.limits[key] as number[];

  return (
    <div className="flex flex-col gap-5 pb-24">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">Changes apply to the next download and are kept when songdrop restarts.</p>
      </div>

      <Section title="Output" description="Where finished songs go. Each one also stays downloadable from the Downloads screen.">
        <Row label="Send songs to the output folder" help="Off: songs are only downloadable from this page.">
          <Switch checked={draft.deliver} onCheckedChange={(value) => set("deliver", value)} />
        </Row>
        <Row
          label="Output folder"
          help={
            <>
              Inside <code className="font-mono">{saved.output_root}</code>. In this stack, <code className="font-mono">music/songdrop</code> is musicguard's inbox, which adds songs to your library.
            </>
          }
          wide
        >
          <div className={cn("flex items-center rounded-md border border-input bg-black/15 focus-within:ring-2 focus-within:ring-ring", !draft.deliver && "opacity-50")}>
            <span className="flex items-center gap-1.5 border-r border-input px-3 font-mono text-xs text-muted-foreground">
              <FolderOpen className="size-3.5" /> {saved.output_root}/
            </span>
            <input
              value={draft.output_folder}
              disabled={!draft.deliver}
              onChange={(event) => set("output_folder", event.target.value)}
              className="h-9 min-w-0 flex-1 bg-transparent px-3 font-mono text-sm outline-none"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          {!saved.output_folder_exists && draft.output_folder === saved.output_folder && (
            <p className="mt-1.5 text-xs text-muted-foreground">This folder doesn't exist yet; it is made with the first song.</p>
          )}
        </Row>
      </Section>

      <Section title="Downloads" description="How much songdrop does at once, and how long it keeps finished downloads.">
        <NumberRow label="Keep downloads for" unit="hours" help="Then the download and its files leave this page. Copies in the output folder stay." value={draft.retention_hours} range={limit("retention_hours")} onChange={(v) => set("retention_hours", v)} />
        <NumberRow label="Most songs per link" unit="songs" help="A longer playlist is refused rather than half done." value={draft.max_items} range={limit("max_items")} onChange={(v) => set("max_items", v)} />
        <NumberRow label="Links at once" unit="links" help="How many links download at the same time." value={draft.concurrent_jobs} range={limit("concurrent_jobs")} onChange={(v) => set("concurrent_jobs", v)} />
        <NumberRow label="Spotify songs at once" unit="songs" help="Per Spotify link. More is faster, and more likely to be refused by YouTube." value={draft.spotdl_parallel} range={limit("spotdl_parallel")} onChange={(v) => set("spotdl_parallel", v)} />
        <NumberRow label="Time limit per link" unit="minutes" help="A download still running then is stopped." value={draft.job_timeout_minutes} range={limit("job_timeout_minutes")} onChange={(v) => set("job_timeout_minutes", v)} />
      </Section>

      <Section title="Later tries" description="A song that fails is tried again on its own, this long after each failure. With none, only Retry now tries again.">
        <RetryEditor
          value={draft.retry_minutes}
          max={saved.limits.retries as number}
          range={limit("retry_minutes")}
          onChange={(value) => set("retry_minutes", value)}
        />
      </Section>

      <Section title="Notifications" description="Missing songs, songs a later try brought back, and songs songdrop gave up on.">
        <WebhookRow
          isSet={saved.discord_webhook_set}
          value={draft.discord_webhook}
          onChange={(value) => set("discord_webhook", value)}
        />
        <Row label="Page address" help="Added to messages as a link to this page." wide>
          <Input value={draft.public_url} onChange={(event) => set("public_url", event.target.value)} placeholder="http://songdrop.homelab.lan" spellCheck={false} />
        </Row>
      </Section>

      <DefaultsSection value={draft.defaults} onChange={(value) => set("defaults", value)} />

      <CookiesCard />

      {dirty && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/85 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl">
          <div className="mx-auto flex h-16 w-full max-w-3xl items-center justify-between gap-3 px-4">
            <p className="text-sm text-muted-foreground">
              {Object.keys(patch).length} unsaved change{Object.keys(patch).length === 1 ? "" : "s"}
            </p>
            <div className="flex gap-2">
              <Button variant="ghost" disabled={saving} onClick={() => setDraft(draftOf(saved))}>
                Discard
              </Button>
              <Button disabled={saving} onClick={save}>
                {saving ? <Loader2 className="animate-spin" /> : <Check />} Save
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <Card className="p-0">
      <div className="border-b border-border px-4 py-3.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{description}</p>
      </div>
      <div className="divide-y divide-border">{children}</div>
    </Card>
  );
}

function Row({ label, help, children, wide = false }: { label: string; help?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className={cn("flex gap-3 px-4 py-3.5", wide ? "flex-col" : "items-center justify-between")}>
      <div className="min-w-0">
        <Label className="text-sm">{label}</Label>
        {help && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{help}</p>}
      </div>
      <div className={cn(wide ? "w-full" : "shrink-0")}>{children}</div>
    </div>
  );
}

function NumberRow({ label, unit, help, value, range, onChange }: {
  label: string; unit: string; help: string; value: number; range: number[]; onChange: (value: number) => void;
}) {
  const [low, high] = range;
  const invalid = !Number.isInteger(value) || value < low || value > high;
  return (
    <Row label={label} help={invalid ? `Between ${low} and ${high}.` : help}>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          inputMode="numeric"
          min={low}
          max={high}
          value={Number.isNaN(value) ? "" : value}
          onChange={(event) => onChange(event.target.valueAsNumber)}
          aria-invalid={invalid}
          className={cn("h-9 w-24 text-right tabular-nums", invalid && "border-destructive")}
        />
        <span className="w-14 text-xs text-muted-foreground">{unit}</span>
      </div>
    </Row>
  );
}

function RetryEditor({ value, max, range, onChange }: { value: number[]; max: number; range: number[]; onChange: (value: number[]) => void }) {
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState<"min" | "h">("min");
  const minutes = Math.round(Number(amount) * (unit === "h" ? 60 : 1));
  const valid = amount !== "" && minutes >= range[0] && minutes <= range[1];

  return (
    <div className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        {value.length === 0 && <span className="text-sm text-muted-foreground">No later tries.</span>}
        {value.map((delay, position) => (
          <Badge key={position} variant="muted" className="h-8 gap-1.5 pr-1 pl-3 text-sm">
            <span className="text-muted-foreground">Try {position + 2}:</span> {duration(delay)} later
            <button
              type="button"
              aria-label={`Remove try ${position + 2}`}
              onClick={() => onChange(value.filter((_, at) => at !== position))}
              className="ml-0.5 flex size-6 items-center justify-center rounded hover:bg-white/10"
            >
              <X className="size-3.5" />
            </button>
          </Badge>
        ))}
      </div>
      {value.length < max && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!valid) return;
            onChange([...value, minutes]);
            setAmount("");
          }}
        >
          <Input type="number" inputMode="decimal" min={0} value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="30" className="h-9 w-24 text-right" aria-label="Later try after" />
          <select value={unit} onChange={(event) => setUnit(event.target.value as "min" | "h")} className="h-9 rounded-md border border-input bg-black/15 px-2 text-sm" aria-label="Unit">
            <option value="min">minutes</option>
            <option value="h">hours</option>
          </select>
          <Button type="submit" variant="secondary" size="sm" disabled={!valid}>
            <Plus /> Add a try
          </Button>
        </form>
      )}
    </div>
  );
}

function WebhookRow({ isSet, value, onChange }: { isSet: boolean; value: string | undefined; onChange: (value: string | undefined) => void }) {
  const removing = value === "";
  return (
    <Row
      label="Discord webhook"
      help="Write-only: once saved it is never shown again. Paste a new one to replace it."
      wide
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={removing ? "warning" : isSet ? "success" : "muted"}>{removing ? "Removed on save" : isSet ? "Set" : "Not set"}</Badge>
        <Input
          type="password"
          value={removing ? "" : (value ?? "")}
          onChange={(event) => onChange(event.target.value || undefined)}
          placeholder={isSet ? "Paste a new webhook to replace it" : "https://discord.com/api/webhooks/…"}
          autoComplete="off"
          className="h-9 min-w-0 flex-1"
        />
        {isSet && !removing && (
          <Button variant="ghost" size="sm" onClick={() => onChange("")}>
            Remove
          </Button>
        )}
        {removing && (
          <Button variant="ghost" size="sm" onClick={() => onChange(undefined)}>
            Keep it
          </Button>
        )}
      </div>
    </Row>
  );
}

function DefaultsSection({ value, onChange }: { value: Record<Engine, OptionValues>; onChange: (value: Record<Engine, OptionValues>) => void }) {
  const spec = useOptionsSpec();
  const [engine, setEngine] = useState<Engine>("spotdl");
  if (!spec) return null;
  // A clip is for one video, not a default.
  const fields = spec.engines[engine].fields.filter((field) => field.type !== "time");
  const builtIn = Object.fromEntries(spec.engines[engine].fields.map((field) => [field.key, field.builtin]));

  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3.5">
        <div>
          <h2 className="text-sm font-semibold">Default options</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">Where each download's options start. A download can still change them.</p>
        </div>
        <div role="tablist" className="flex gap-1 rounded-lg border border-border p-0.5">
          {(["spotdl", "ytdlp"] as Engine[]).map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={engine === key}
              onClick={() => setEngine(key)}
              className={cn("h-7 rounded-md px-3 text-xs font-medium text-muted-foreground", engine === key && "bg-accent text-foreground")}
            >
              {key === "spotdl" ? "Spotify links" : "Other links"}
            </button>
          ))}
        </div>
      </div>
      <div className="px-4 pb-4">
        <OptionsPanel
          title={spec.engines[engine].label}
          fields={fields}
          values={{ ...builtIn, ...value[engine] }}
          onChange={(next) => onChange({ ...value, [engine]: next })}
          onReset={() => onChange({ ...value, [engine]: builtIn })}
          plain
        />
      </div>
    </Card>
  );
}
