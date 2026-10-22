import { ChevronRight, Loader2, Send, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, type Connection, type ConnectionType, type ConnectionTypes, errorText } from "@/lib/api";

import { iconFor } from "./icons";

/** The form; what ConnectionIn takes, every field filled. */
type Draft = { name: string; type: string; enabled: boolean; events: string[]; config: Record<string, string> };

function draftFor(type: ConnectionType, events: ConnectionTypes["events"], existing?: Connection): Draft {
  if (existing) {
    return { name: existing.name, type: existing.type, enabled: existing.enabled, events: [...existing.events], config: { ...existing.config } };
  }
  return { name: type.label, type: type.type, enabled: true, events: events.filter((e) => e.default).map((e) => e.name), config: {} };
}

/**
 * Adds or changes a connection. A new one starts with its type; the type's
 * fields come from the API (GET /connections/types), so a new type needs no
 * change here. Secrets are never shown: left empty, the saved one is kept.
 */
export function ConnectionDialog({
  open,
  schema,
  existing,
  onClose,
  onSaved,
}: {
  open: boolean;
  schema: ConnectionTypes;
  existing?: Connection;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [type, setType] = useState<ConnectionType | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "remove" | null>(null);

  useEffect(() => {
    if (!open) return;
    const known = existing ? (schema.types.find((t) => t.type === existing.type) ?? null) : null;
    setType(known);
    setDraft(known ? draftFor(known, schema.events, existing) : null);
  }, [open, existing, schema]);

  const choose = (chosen: ConnectionType) => {
    setType(chosen);
    setDraft(draftFor(chosen, schema.events));
  };

  const run = async (kind: "save" | "test" | "remove", action: () => Promise<void>) => {
    setBusy(kind);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  };

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    void run("save", async () => {
      try {
        if (existing) await api.updateConnection(existing.id, draft);
        else await api.createConnection(draft);
        toast.success(existing ? "Connection saved" : "Connection added");
        onSaved();
      } catch (caught) {
        toast.error("Couldn't save the connection", { description: errorText(caught) });
      }
    });
  };

  const test = () =>
    draft &&
    run("test", async () => {
      try {
        const result = await api.testConnection(draft, existing?.id);
        if (result.ok) toast.success("Test message sent", { description: result.message });
        else toast.error("The test message didn't go through", { description: result.message });
      } catch (caught) {
        toast.error("Couldn't send a test message", { description: errorText(caught) });
      }
    });

  const remove = () =>
    existing &&
    window.confirm(`Remove ${existing.name}? It will get no more messages.`) &&
    run("remove", async () => {
      try {
        await api.removeConnection(existing.id);
        toast.success("Connection removed");
        onSaved();
      } catch (caught) {
        toast.error("Couldn't remove it", { description: errorText(caught) });
      }
    });

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-xl">
        {!type || !draft ? (
          <TypeChooser schema={schema} onChoose={choose} />
        ) : (
          <form onSubmit={save} className="flex min-h-0 flex-1 flex-col">
            <DialogHeader>
              <DialogTitle>{existing ? `${existing.name}` : `Add a ${type.label} connection`}</DialogTitle>
              <DialogDescription>{type.description}</DialogDescription>
            </DialogHeader>
            <DialogBody className="flex flex-col gap-5">
              <div className="flex items-end gap-4">
                <div className="flex flex-1 flex-col gap-2">
                  <Label htmlFor="connection-name">Name</Label>
                  <Input id="connection-name" value={draft.name} maxLength={60} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </div>
                <div className="flex h-10 items-center gap-2">
                  <Switch id="connection-enabled" checked={draft.enabled} onCheckedChange={(enabled) => setDraft({ ...draft, enabled })} />
                  <Label htmlFor="connection-enabled">On</Label>
                </div>
              </div>

              {type.fields.map((field) => {
                const isSet = existing?.secrets_set.includes(field.key);
                return (
                  <div key={field.key} className="flex flex-col gap-2">
                    <Label htmlFor={`field-${field.key}`}>
                      {field.label}
                      {field.secret && isSet && <span className="text-xs font-normal text-success">set</span>}
                    </Label>
                    <Input
                      id={`field-${field.key}`}
                      type={field.secret ? "password" : field.kind === "text" ? "text" : "url"}
                      value={draft.config[field.key] ?? ""}
                      onChange={(e) => setDraft({ ...draft, config: { ...draft.config, [field.key]: e.target.value } })}
                      placeholder={field.secret && isSet ? "Saved. Paste a new one to replace it." : field.placeholder}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    {field.help && <p className="text-xs leading-relaxed text-muted-foreground">{field.help}</p>}
                  </div>
                );
              })}

              <fieldset className="flex flex-col gap-1">
                <legend className="mb-2 text-sm font-medium">Send a message when</legend>
                {schema.events.map((item) => (
                  <label key={item.name} className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-accent/50">
                    <Checkbox
                      className="mt-0.5"
                      checked={draft.events.includes(item.name)}
                      onCheckedChange={(checked) =>
                        setDraft({
                          ...draft,
                          events: checked ? [...draft.events, item.name] : draft.events.filter((name) => name !== item.name),
                        })
                      }
                    />
                    <span className="min-w-0">
                      <span className="block text-sm">{item.label}</span>
                      <span className="block text-xs text-muted-foreground">{item.description}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            </DialogBody>
            <DialogFooter>
              {existing && (
                <Button type="button" variant="destructive" size="sm" disabled={!!busy} onClick={remove}>
                  {busy === "remove" ? <Loader2 className="animate-spin" /> : <Trash2 />} Remove
                </Button>
              )}
              <div className="ml-auto flex gap-2">
                <Button type="button" variant="secondary" disabled={!!busy} onClick={test}>
                  {busy === "test" ? <Loader2 className="animate-spin" /> : <Send />} Test
                </Button>
                <Button type="submit" disabled={!!busy || !draft.name.trim()}>
                  {busy === "save" && <Loader2 className="animate-spin" />} Save
                </Button>
              </div>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function TypeChooser({ schema, onChoose }: { schema: ConnectionTypes; onChoose: (type: ConnectionType) => void }) {
  return (
    <>
      <DialogHeader>
        <DialogTitle>Add a connection</DialogTitle>
        <DialogDescription>Where should songdrop send its messages?</DialogDescription>
      </DialogHeader>
      <DialogBody className="grid gap-2 sm:grid-cols-2">
        {schema.types.map((type) => {
          const Icon = iconFor(type.type);
          return (
            <button
              key={type.type}
              type="button"
              onClick={() => onChoose(type)}
              className="flex items-start gap-3 rounded-xl border border-border p-4 text-left transition-colors hover:border-primary/50 hover:bg-accent/50"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-violet-200">
                <Icon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1 text-sm font-medium">
                  {type.label} <ChevronRight className="size-3.5 text-muted-foreground" />
                </span>
                <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{type.description}</span>
              </span>
            </button>
          );
        })}
      </DialogBody>
    </>
  );
}
