import { useQueryClient } from "@tanstack/react-query";
import { FolderOutput, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DestinationDialog } from "@/components/destinations/DestinationDialog";
import { QUALITY } from "@/components/jobs/labels";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type Destination, errorText } from "@/lib/api";
import { keys, useDestinations, useSettings } from "@/lib/queries";

export default function DestinationsPage() {
  const client = useQueryClient();
  const { data, error, isPending, refetch } = useDestinations();
  const { data: settings } = useSettings();
  const [editing, setEditing] = useState<Destination | undefined>();
  const [open, setOpen] = useState(false);

  const reload = () => {
    void client.invalidateQueries({ queryKey: keys.destinations });
    void client.invalidateQueries({ queryKey: keys.settings });
  };
  const run = async (work: () => Promise<unknown>, done: string) => {
    try {
      await work();
      toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    reload();
  };

  return (
    <>
      <PageHeader title="Destinations" description="Where downloads go, named: apps send a destination's name, never a path."
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus /> Add</Button>} />
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.destinations.length ? (
        <Empty icon={<FolderOutput />} title="No destination yet">Without one, downloads are kept here for whoever asked to fetch.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.destinations.map((item) => {
            const isDefault = settings?.default_destination === item.slug;
            return (
              <li key={item.slug} className="flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{item.name}</p>
                    <code className="text-xs text-muted-foreground">{item.slug}</code>
                    {isDefault && <Badge variant="accent">Default</Badge>}
                  </div>
                  <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground" title={`media/${item.folder}/${item.template}`}>
                    media/{item.folder}/{item.template}
                  </p>
                  {(item.options.quality || item.options.container) && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {[item.options.quality && QUALITY[item.options.quality], item.options.container?.toUpperCase()].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
                {!isDefault && (
                  <Button variant="ghost" size="icon-sm" title="Make it the default" aria-label={`Make ${item.name} the default`}
                          onClick={() => void run(() => api.saveSettings({ default_destination: item.slug }), `${item.name} is the default`)}>
                    <Star />
                  </Button>
                )}
                <Button variant="ghost" size="icon-sm" onClick={() => { setEditing(item); setOpen(true); }} aria-label={`Change ${item.name}`}>
                  <Pencil />
                </Button>
                <Button variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`}
                        onClick={() => { if (window.confirm(`Remove ${item.name}? Its folder and files stay.`)) void run(() => api.deleteDestination(item.slug), "Removed"); }}>
                  <Trash2 />
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <DestinationDialog saved={editing} open={open} onOpenChange={setOpen} onSaved={reload} />
    </>
  );
}
