import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Check, FlaskConical, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SourceDialog } from "@/components/settings/SourceDialog";
import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, errorText, type Source, type SourceIn } from "@/lib/api";
import { keys, useSources, useSourceTypes } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

/** How a source is doing: green, it answered last time; red, it failed; grey, never asked. Hover: what it said. */
function Health({ item }: { item: Source }) {
  const last = item.last;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} aria-label={!last ? "Not asked yet" : last.ok ? "Answering" : "Failing"}
              className={cn("relative flex size-3 shrink-0 rounded-full", !last ? "bg-muted-foreground/40" : last.ok ? "bg-success" : "bg-destructive")}>
          {last && !last.ok && <span className="absolute inset-0 animate-ping rounded-full bg-destructive/60" />}
        </span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-72">
        {!last ? "Not asked yet: Test it." : (
          <div className="flex flex-col gap-0.5">
            <span className="font-medium">{last.ok ? "Answering" : "Failing"} · {timeAgo(last.at)}</span>
            <span className="text-muted-foreground">{last.message}</span>
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Settings, Sources: every kind of source stanzarr knows, the ones in use in
 * the order they are asked. One not in use is added from its card (+); one in
 * use can be tested, turned off, moved, changed or removed.
 */
export default function SourcesPage() {
  const client = useQueryClient();
  const { data, error, refetch, isPending } = useSources();
  const { data: types } = useSourceTypes();
  const [editing, setEditing] = useState<Source | undefined>();
  const [adding, setAdding] = useState<string | undefined>();
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const reload = () => {
    void client.invalidateQueries({ queryKey: keys.sources });
    void client.invalidateQueries({ queryKey: keys.status });
  };
  const run = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    reload();
  };
  const move = (index: number, by: -1 | 1) => {
    if (!data) return;
    const ids = data.items.map((item) => item.id);
    [ids[index], ids[index + by]] = [ids[index + by], ids[index]];
    void run(() => api.orderSources(ids));
  };
  const body = (item: Source, enabled = item.enabled): SourceIn =>
    ({ name: item.name, type: item.type as SourceIn["type"], enabled, config: item.config as Record<string, string> });
  const test = async (item: Source) => {
    setTesting(item.id);
    try {
      const result = await api.testSource(body(item), item.id);
      (result.ok ? toast.success : toast.error)(`${item.name}: ${result.message}`);
    } catch (caught) {
      toast.error(errorText(caught));
    } finally {
      setTesting(null);
      reload();
    }
  };
  const add = (type: string) => {
    const kind = types?.types.find((t) => t.type === type);
    if (!kind) return;
    // A kind needing nothing (no token): added at once; else its form first.
    if (!kind.fields.some((f) => f.required)) {
      void run(() => api.createSource({ name: kind.label, type: type as SourceIn["type"], enabled: true, config: {} }), `${kind.label} added: asked last`);
    } else {
      setEditing(undefined);
      setAdding(type);
      setOpen(true);
    }
  };

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const used = new Set(data.items.map((item) => item.type));

  return (
    <>
      <PageHeader eyebrow="Settings" title="Sources"
                  description="Where stanzarr asks for the lyrics a song lacks, top to bottom. Synced lyrics end the search; words without timings are kept until one has them synced." />

      <h2 className="mb-2 text-sm font-medium">In use, in this order</h2>
      {!data.items.length ? (
        <p className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
          None yet: songs get only the lyrics beside them and what the AI syncs. Add one below.
        </p>
      ) : (
        <ol className="flex flex-col gap-2">
          {data.items.map((item, index) => (
            <li key={item.id} className={cn("flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-3 sm:flex-nowrap sm:p-4", !item.enabled && "opacity-60")}>
              <div className="flex flex-col">
                <Button variant="ghost" size="icon-sm" disabled={index === 0} aria-label={`Ask ${item.name} sooner`} onClick={() => move(index, -1)}><ArrowUp /></Button>
                <Button variant="ghost" size="icon-sm" disabled={index === data.items.length - 1} aria-label={`Ask ${item.name} later`} onClick={() => move(index, 1)}><ArrowDown /></Button>
              </div>
              <Health item={item} />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{item.name}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {item.found_songs ? `Lyrics for ${item.found_songs.toLocaleString()} songs` : "No lyrics found yet"}
                  {item.last_found ? ` · last found ${timeAgo(item.last_found)}` : ""}
                  {item.last ? ` · last asked ${timeAgo(item.last.at)}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <Button variant="secondary" size="sm" disabled={testing === item.id} onClick={() => void test(item)} title="Ask it for a song it should know">
                  {testing === item.id ? <Loader2 className="animate-spin" /> : <FlaskConical />} Test
                </Button>
                <Switch checked={item.enabled} aria-label={item.enabled ? `Turn ${item.name} off` : `Turn ${item.name} on`} className="mx-1"
                        onCheckedChange={(enabled) => void run(() => api.updateSource(item.id, body(item, enabled)))} />
                <Button variant="ghost" size="icon-sm" onClick={() => { setAdding(undefined); setEditing(item); setOpen(true); }} aria-label={`Change ${item.name}`}><Pencil /></Button>
                <Button variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`}
                        onClick={() => { if (window.confirm(`Remove ${item.name}? Lyrics it found stay.`)) void run(() => api.deleteSource(item.id), "Removed"); }}>
                  <Trash2 />
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}

      {types && (
        <>
          <h2 className="mt-8 mb-2 text-sm font-medium">Every source</h2>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {types.types.map((kind) => {
              const on = used.has(kind.type);
              return (
                <li key={kind.type}>
                  <button type="button" disabled={on} onClick={() => add(kind.type)}
                          aria-label={on ? `${kind.label}: in use` : `Add ${kind.label}`}
                          className={cn("group relative flex h-full w-full flex-col gap-1 rounded-xl border p-3 text-left transition",
                                        on ? "border-success/30 bg-success/5" : "border-border bg-card hover:border-primary/50 hover:bg-accent/50")}>
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{kind.label}</span>
                      {on ? (
                        <span className="flex items-center gap-1 rounded-full bg-success/15 px-2 py-0.5 text-[11px] text-success"><Check className="size-3" /> In use</span>
                      ) : (
                        <span className="flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100">
                          <Plus className="size-4" />
                        </span>
                      )}
                    </span>
                    <span className="text-xs text-muted-foreground">{kind.description}</span>
                    <span className="mt-auto pt-1 text-[11px] text-muted-foreground/80">
                      {kind.synced ? "Synced and plain lyrics" : "Words only"}{kind.fields.some((f) => f.required) ? " · needs a token" : " · nothing to set up"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {types && <SourceDialog types={types} saved={editing} adding={adding} open={open} onOpenChange={setOpen} onSaved={reload} />}
    </>
  );
}
