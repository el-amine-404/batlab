import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Film, Music2, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Family, type Integration } from "@/lib/api";
import { keys, useIntegrations, useTypes } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

import { IntegrationDialog } from "./IntegrationDialog";

const COPY: Record<Family, { title: string; description: string; empty: string }> = {
  services: {
    title: "Services",
    description: "The apps that download what is asked for. A request goes to the first one that takes its kind; when that one fails, to the next.",
    empty: "No service yet: requests wait until there is one.",
  },
  libraries: {
    title: "Libraries",
    description: "Where songs and music videos already are. clipseerr reads them every 10 minutes: what they hold gets a green check, everywhere.",
    empty: "No library yet: only what clipseerr asked for is marked.",
  },
  credits: {
    title: "Credits",
    description: "Where a song's credits are looked for, top to bottom: the first with credits wins, and the song's panel says which were asked.",
    empty: "No source yet: songs show no credits.",
  },
  notifications: {
    title: "Notifications",
    description: "Where clipseerr tells about requests: made, done, failed.",
    empty: "No notification yet: requests happen quietly.",
  },
};

export function IntegrationsScreen({ family }: { family: Family }) {
  const client = useQueryClient();
  const { data, error, refetch, isPending } = useIntegrations(family);
  const { data: types } = useTypes(family);
  const [editing, setEditing] = useState<Integration | undefined>();
  const [open, setOpen] = useState(false);
  const copy = COPY[family];

  const reload = () => {
    void client.invalidateQueries({ queryKey: keys.integrations(family) });
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

  const move = (index: number, by: number) => {
    const ids = data!.items.map((item) => item.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(index + by, 0, moved);
    void run(() => api.orderIntegrations(family, ids));
  };

  const toggle = (item: Integration, enabled: boolean) =>
    run(() => api.updateIntegration(family, item.id, { name: item.name, type: item.type, enabled, events: item.events, config: item.config }));

  return (
    <>
      <PageHeader eyebrow="Settings" title={copy.title} description={copy.description}
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }} disabled={!types}><Plus /> Add</Button>} />
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.items.length ? (
        <Empty icon={<Plus />} title={copy.empty} />
      ) : (
        <ol className="flex flex-col gap-2">
          {data.items.map((item, index) => (
            <li key={item.id} className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4", !item.enabled && "opacity-60")}>
              {(family === "services" || family === "credits") && (
                <div className="flex flex-col">
                  <Button variant="ghost" size="icon-sm" disabled={index === 0} onClick={() => move(index, -1)} aria-label="Ask it earlier">
                    <ArrowUp />
                  </Button>
                  <Button variant="ghost" size="icon-sm" disabled={index === data.items.length - 1} onClick={() => move(index, 1)} aria-label="Ask it later">
                    <ArrowDown />
                  </Button>
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {(family === "services" || family === "credits") && <span className="text-xs font-semibold text-muted-foreground tabular-nums">{index + 1}</span>}
                  <p className="font-medium">{item.name}</p>
                  <Badge variant="muted">{item.type_label}</Badge>
                  {item.kinds?.map((kind) => (
                    <Badge key={kind} variant="accent">{kind === "audio" ? <Music2 /> : <Film />}{kind === "audio" ? "Audio" : "Video"}</Badge>
                  ))}
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.summary}</p>
                {item.last && (
                  <p className={cn("mt-1 truncate text-xs", item.last.ok ? "text-success" : "text-destructive")} title={item.last.message}>
                    {item.last.ok ? "OK" : "Failed"} {timeAgo(item.last.at)}: {item.last.message}
                  </p>
                )}
              </div>
              <Switch checked={item.enabled} onCheckedChange={(on) => void toggle(item, on)} aria-label={item.enabled ? "Turn off" : "Turn on"} />
              <Button variant="ghost" size="icon-sm" onClick={() => { setEditing(item); setOpen(true); }} aria-label={`Change ${item.name}`}>
                <Pencil />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`}
                      onClick={() => { if (window.confirm(`Remove ${item.name}?`)) void run(() => api.removeIntegration(family, item.id), "Removed"); }}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ol>
      )}
      {types && <IntegrationDialog family={family} types={types} saved={editing} open={open} onOpenChange={setOpen} onSaved={reload} />}
    </>
  );
}
