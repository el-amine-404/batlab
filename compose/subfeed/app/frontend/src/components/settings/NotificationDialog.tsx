import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Notification, type NotificationField, type NotificationIn, type NotificationTypes, type TestResult } from "@/lib/api";
import { cn } from "@/lib/utils";

type Values = Record<string, string>;

function initial(fields: NotificationField[], saved?: Notification): Values {
  const values: Values = {};
  for (const field of fields) values[field.key] = field.secret ? "" : (saved?.config[field.key] ?? field.default ?? "");
  return values;
}

/** Adds or changes one notification; its form is its type's fields (GET /notifications/types). */
export function NotificationDialog({ types, saved, open, onOpenChange, onSaved }: {
  types: NotificationTypes;
  saved?: Notification;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const defaults = types.events.filter((e) => e.default).map((e) => e.name);
  const [type, setType] = useState(saved?.type ?? "");
  const kind = types.types.find((t) => t.type === type);
  const [name, setName] = useState(saved?.name ?? "");
  const [enabled, setEnabled] = useState(saved?.enabled ?? true);
  const [events, setEvents] = useState<string[]>(saved?.events ?? defaults);
  const [values, setValues] = useState<Values>(() => (kind ? initial(kind.fields, saved) : {}));
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setType(saved?.type ?? "");
    setName(saved?.name ?? "");
    setEnabled(saved?.enabled ?? true);
    setEvents(saved?.events ?? types.events.filter((e) => e.default).map((e) => e.name));
    const chosen = types.types.find((t) => t.type === saved?.type);
    setValues(chosen ? initial(chosen.fields, saved) : {});
    setResult(null);
  }, [open, saved, types]);

  const pick = (next: string) => {
    const chosen = types.types.find((t) => t.type === next);
    setType(next);
    setValues(chosen ? initial(chosen.fields) : {});
    if (!name || types.types.some((t) => t.label === name)) setName(chosen?.label ?? "");
    setResult(null);
  };

  const body = (): NotificationIn => ({ name: name.trim() || kind?.label || type, type, enabled, events, config: values });

  const test = async () => {
    setBusy("test");
    setResult(null);
    try {
      setResult(await api.testNotification(body(), saved?.id));
    } catch (error) {
      setResult({ ok: false, message: errorText(error) });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy("save");
    try {
      if (saved) await api.updateNotification(saved.id, body());
      else await api.createNotification(body());
      toast.success(saved ? "Saved" : "Added");
      onSaved();
      onOpenChange(false);
    } catch (error) {
      toast.error(errorText(error, "Couldn't save it."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{saved ? `Change ${saved.name}` : "Add a notification"}</DialogTitle>
          <DialogDescription>Where subfeed tells about its feeds: one failing, working again, new posts.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          {!saved && (
            <div className="grid gap-2 sm:grid-cols-3">
              {types.types.map((item) => (
                <button key={item.type} type="button" onClick={() => pick(item.type)}
                        className={cn("rounded-lg border p-3 text-left transition",
                                      type === item.type ? "border-orange-400/60 bg-orange-400/10" : "border-border hover:bg-accent")}>
                  <p className="text-sm font-medium">{item.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{item.description}</p>
                </button>
              ))}
            </div>
          )}
          {kind && (
            <>
              {kind.homepage && (
                <a href={kind.homepage} target="_blank" rel="noreferrer" className="-mt-2 flex items-center gap-1 text-xs text-orange-300 hover:underline">
                  About {kind.label} <ExternalLink className="size-3" />
                </a>
              )}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="notification-name">Name</Label>
                <Input id="notification-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
              </div>
              {kind.fields.map((field) => (
                <div key={field.key} className="flex flex-col gap-1.5">
                  <Label htmlFor={`field-${field.key}`}>
                    {field.label} {!field.required && <span className="font-normal text-muted-foreground">(optional)</span>}
                  </Label>
                  <Input id={`field-${field.key}`} type={field.secret ? "password" : field.kind.includes("url") ? "url" : "text"}
                         value={values[field.key] ?? ""} autoComplete="off" spellCheck={false}
                         onChange={(e) => setValues((old) => ({ ...old, [field.key]: e.target.value }))}
                         placeholder={field.secret && saved?.secrets_set.includes(field.key) ? "Saved; leave empty to keep it" : field.placeholder} />
                  {field.help && <p className="text-xs text-muted-foreground">{field.help}</p>}
                </div>
              ))}
              <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">Tell about</legend>
                {types.events.map((event) => (
                  <label key={event.name} className="flex items-start gap-2.5 text-sm">
                    <Checkbox checked={events.includes(event.name)} className="mt-0.5"
                              onCheckedChange={(on) => setEvents((old) => (on ? [...old, event.name] : old.filter((e) => e !== event.name)))} />
                    <span><span className="font-medium">{event.label}</span> <span className="text-muted-foreground">— {event.description}</span></span>
                  </label>
                ))}
              </fieldset>
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="notification-enabled">On</Label>
                <Switch id="notification-enabled" checked={enabled} onCheckedChange={setEnabled} />
              </div>
              {result && (
                <p className={cn("flex items-start gap-2 rounded-lg p-3 text-sm", result.ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive")}>
                  {result.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <XCircle className="mt-0.5 size-4 shrink-0" />}
                  {result.message}
                </p>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="secondary" onClick={() => void test()} disabled={!kind || !!busy}>
            {busy === "test" && <Loader2 className="animate-spin" />} Send a test
          </Button>
          <Button onClick={() => void save()} disabled={!kind || !!busy}>
            {busy === "save" && <Loader2 className="animate-spin" />} {saved ? "Save" : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
