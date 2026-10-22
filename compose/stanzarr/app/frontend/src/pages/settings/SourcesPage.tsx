import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Cable, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SourceDialog } from "@/components/settings/SourceDialog";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Source, type SourceIn } from "@/lib/api";
import { keys, useSources, useSourceTypes } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

/** Settings, Sources: the add-ons asked for lyrics, first to last. */
export default function SourcesPage() {
  const client = useQueryClient();
  const { data, error, refetch, isPending } = useSources();
  const { data: types } = useSourceTypes();
  const [editing, setEditing] = useState<Source | undefined>();
  const [open, setOpen] = useState(false);

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

  return (
    <>
      <PageHeader eyebrow="Settings" title="Sources"
                  description="Where stanzarr asks for the lyrics a song lacks, first to last: synced lyrics from one end the search, plain words are kept until a later one has them synced."
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }} disabled={!types}><Plus /> Add</Button>} />
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.items.length ? (
        <Empty icon={<Cable />} title="No source">Songs get only the lyrics already beside them, and what the AI syncs. Add LRCLIB to find the rest.</Empty>
      ) : (
        <ol className="flex flex-col gap-2">
          {data.items.map((item, index) => (
            <li key={item.id} className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4", !item.enabled && "opacity-60")}>
              <div className="flex flex-col">
                <Button variant="ghost" size="icon-sm" disabled={index === 0} aria-label={`Ask ${item.name} sooner`} onClick={() => move(index, -1)}>
                  <ArrowUp />
                </Button>
                <Button variant="ghost" size="icon-sm" disabled={index === data.items.length - 1} aria-label={`Ask ${item.name} later`}
                        onClick={() => move(index, 1)}>
                  <ArrowDown />
                </Button>
              </div>
              <span className="w-5 text-center text-sm text-muted-foreground tabular-nums">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{item.name}</p>
                  <Badge variant="muted">{item.type_label}</Badge>
                </div>
                {item.config.url && <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.config.url}</p>}
                {item.last && (
                  <p className={cn("mt-1 truncate text-xs", item.last.ok ? "text-muted-foreground" : "text-destructive")} title={item.last.message}>
                    {item.last.ok ? "Answered" : "Failed"} {timeAgo(item.last.at)}: {item.last.message}
                  </p>
                )}
              </div>
              <Switch checked={item.enabled} aria-label={item.enabled ? "Turn off" : "Turn on"}
                      onCheckedChange={(enabled) => void run(() => api.updateSource(item.id, { name: item.name, type: item.type as SourceIn["type"], enabled, config: item.config }))} />
              <Button variant="ghost" size="icon-sm" onClick={() => { setEditing(item); setOpen(true); }} aria-label={`Change ${item.name}`}>
                <Pencil />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`}
                      onClick={() => { if (window.confirm(`Remove ${item.name}?`)) void run(() => api.deleteSource(item.id), "Removed"); }}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ol>
      )}
      {types && <SourceDialog types={types} saved={editing} open={open} onOpenChange={setOpen} onSaved={reload} />}
    </>
  );
}
