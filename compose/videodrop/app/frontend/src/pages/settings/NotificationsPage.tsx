import { useQueryClient } from "@tanstack/react-query";
import { Bell, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { NotificationDialog } from "@/components/settings/NotificationDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Notification } from "@/lib/api";
import { keys, useNotifications, useNotificationTypes } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

export default function NotificationsPage() {
  const client = useQueryClient();
  const { data, error, refetch, isPending } = useNotifications();
  const { data: types } = useNotificationTypes();
  const [editing, setEditing] = useState<Notification | undefined>();
  const [open, setOpen] = useState(false);

  const reload = () => void client.invalidateQueries({ queryKey: keys.notifications });
  const run = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    reload();
  };
  const label = (name: string) => types?.events.find((e) => e.name === name)?.label ?? name;

  return (
    <>
      <PageHeader eyebrow="Settings" title="Notifications"
                  description="Where videodrop tells people: a download failing, done, the disk nearly full. It says what happened; these decide where it goes."
                  actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }} disabled={!types}><Plus /> Add</Button>} />
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.items.length ? (
        <Empty icon={<Bell />} title="No notification yet">Failures still show on the Downloads page.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.items.map((item) => (
            <li key={item.id} className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4", !item.enabled && "opacity-60")}>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{item.name}</p>
                  <Badge variant="muted">{item.type_label}</Badge>
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.summary} · {item.events.map(label).join(", ") || "no event"}</p>
                {item.last && (
                  <p className={cn("mt-1 truncate text-xs", item.last.ok ? "text-success" : "text-destructive")} title={item.last.message}>
                    {item.last.ok ? "OK" : "Failed"} {timeAgo(item.last.at)}: {item.last.message}
                  </p>
                )}
              </div>
              <Switch checked={item.enabled} aria-label={item.enabled ? "Turn off" : "Turn on"}
                      onCheckedChange={(enabled) => void run(() => api.updateNotification(item.id, { name: item.name, type: item.type, enabled, events: item.events, config: item.config }))} />
              <Button variant="ghost" size="icon-sm" onClick={() => { setEditing(item); setOpen(true); }} aria-label={`Change ${item.name}`}>
                <Pencil />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`}
                      onClick={() => { if (window.confirm(`Remove ${item.name}?`)) void run(() => api.deleteNotification(item.id), "Removed"); }}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {types && <NotificationDialog types={types} saved={editing} open={open} onOpenChange={setOpen} onSaved={reload} />}
    </>
  );
}
