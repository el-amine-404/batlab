import { CircleAlert, CircleCheck, Plus } from "lucide-react";
import { useState } from "react";

import { ConnectionDialog } from "@/components/connections/ConnectionDialog";
import { iconFor } from "@/components/connections/icons";
import { Failed } from "@/components/layout/States";
import { SettingsScreen } from "@/components/settings/SettingsScreen";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { api, type Connection, type ConnectionTypes } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { cn, timeAgo } from "@/lib/utils";

/** Where songdrop's messages go: each connection, what it is sent, and how its last message went. */
export function ConnectPage() {
  const schema = useLoad(api.connectionTypes);
  const list = useLoad(api.connections);
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const error = schema.error ?? list.error;

  return (
    <SettingsScreen
      title="Connect"
      description="Where songdrop tells you what happened: songs missing after a download, songs a later try brought back, songs it gave up on. Changes apply at once."
      ready={!!schema.data && !!list.data}
    >
      {error && <Failed message={error} />}
      {schema.data && list.data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {list.data.connections.map((connection) => (
              <ConnectionCard key={connection.id} connection={connection} schema={schema.data!} onOpen={() => setEditing(connection)} />
            ))}
            <button
              type="button"
              onClick={() => setEditing("new")}
              className="flex min-h-32 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
            >
              <Plus className="size-5" /> Add a connection
            </button>
          </div>
          {list.data.connections.length === 0 && (
            <p className="text-sm text-muted-foreground">No connections: songdrop tells nobody when songs are missing.</p>
          )}
          <p className="text-xs text-muted-foreground">
            Types today: {schema.data.types.map((type) => type.label).join(", ")}. A Webhook gets every event as JSON, so any app
            can listen; the format is in the <a href="/docs/guide/connections" className="text-primary hover:underline">docs</a>.
          </p>
          <ConnectionDialog
            open={editing !== null}
            schema={schema.data}
            existing={editing === "new" || editing === null ? undefined : editing}
            onClose={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              void list.reload();
            }}
          />
        </>
      )}
    </SettingsScreen>
  );
}

function ConnectionCard({ connection, schema, onOpen }: { connection: Connection; schema: ConnectionTypes; onOpen: () => void }) {
  const Icon = iconFor(connection.type);
  const labels = new Map(schema.events.map((event) => [event.name, event.label]));
  const last = connection.last;

  return (
    <Card className={cn("p-0 transition-colors hover:border-primary/40", !connection.enabled && "opacity-60")}>
      <button type="button" onClick={onOpen} className="flex h-full w-full flex-col gap-3 p-4 text-left">
        <div className="flex items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-violet-200">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{connection.name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {connection.type_label} · {connection.summary}
            </p>
          </div>
          <Badge variant={connection.enabled ? "success" : "muted"}>{connection.enabled ? "On" : "Off"}</Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {connection.events.length === 0 && <span className="text-xs text-warning">No events chosen: it gets nothing.</span>}
          {connection.events.map((name) => (
            <Badge key={name} variant="muted" className="font-normal">
              {labels.get(name) ?? name}
            </Badge>
          ))}
        </div>
        {last && (
          <p className={cn("flex items-center gap-1.5 text-xs", last.ok ? "text-muted-foreground" : "text-destructive")}>
            {last.ok ? <CircleCheck className="size-3.5" /> : <CircleAlert className="size-3.5" />}
            {last.ok ? `Last message sent ${timeAgo(last.at)}` : `Last message failed ${timeAgo(last.at)}: ${last.error}`}
          </p>
        )}
      </button>
    </Card>
  );
}

