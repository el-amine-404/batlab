import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Family, type Integration, type IntegrationField, type IntegrationIn, type IntegrationTypes, type TestResult } from "@/lib/api";
import { cn } from "@/lib/utils";

type Values = Record<string, string | number | boolean | null>;

function initial(fields: IntegrationField[], saved?: Integration): Values {
  const values: Values = {};
  for (const field of fields) {
    const value = saved?.type && saved.config[field.key] !== undefined ? saved.config[field.key] : field.default;
    values[field.key] = field.secret ? "" : (value ?? (field.kind === "switch" ? false : ""));
  }
  return values;
}

function FieldInput({ field, value, onChange, secretSet }: {
  field: IntegrationField;
  value: Values[string];
  onChange: (value: Values[string]) => void;
  secretSet: boolean;
}) {
  const id = `field-${field.key}`;
  if (field.kind === "switch") {
    return (
      <div className="flex items-center justify-between gap-4">
        <Label htmlFor={id}>{field.label}</Label>
        <Switch id={id} checked={!!value} onCheckedChange={onChange} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>
        {field.label} {!field.required && <span className="font-normal text-muted-foreground">(optional)</span>}
      </Label>
      {field.kind === "select" ? (
        <Select value={String(value ?? "")} onValueChange={onChange}>
          <SelectTrigger id={id}><SelectValue /></SelectTrigger>
          <SelectContent>
            {field.choices.map((choice) => (
              <SelectItem key={choice.value} value={choice.value}>{choice.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          id={id}
          type={field.secret ? "password" : field.kind === "number" ? "number" : field.kind.includes("url") ? "url" : "text"}
          value={String(value ?? "")}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.secret && secretSet ? "Saved; leave empty to keep it" : field.placeholder}
          autoComplete="off"
          spellCheck={false}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
        />
      )}
      {field.help && <p className="text-xs text-muted-foreground">{field.help}</p>}
    </div>
  );
}

/** Adds or changes one service or notification; its form is its type's fields. */
export function IntegrationDialog({ family, types, saved, open, onOpenChange, onSaved }: {
  family: Family;
  types: IntegrationTypes;
  saved?: Integration;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [type, setType] = useState(saved?.type ?? "");
  const kind = types.types.find((t) => t.type === type);
  const [name, setName] = useState(saved?.name ?? "");
  const [enabled, setEnabled] = useState(saved?.enabled ?? true);
  const [events, setEvents] = useState<string[]>(saved?.events ?? types.events.map((e) => e.name));
  const [values, setValues] = useState<Values>(() => (kind ? initial(kind.fields, saved) : {}));
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setType(saved?.type ?? "");
    setName(saved?.name ?? "");
    setEnabled(saved?.enabled ?? true);
    setEvents(saved?.events?.length ? saved.events : types.events.map((e) => e.name));
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

  const body = (): IntegrationIn => ({
    name: name.trim() || kind?.label || type,
    type,
    enabled,
    events: family === "notifications" ? events : null,
    config: values,
  });

  const test = async () => {
    setBusy("test");
    setResult(null);
    try {
      setResult(await api.testIntegration(family, body(), saved?.id));
    } catch (error) {
      setResult({ ok: false, message: errorText(error) });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy("save");
    try {
      if (saved) await api.updateIntegration(family, saved.id, body());
      else await api.createIntegration(family, body());
      toast.success(saved ? "Saved" : "Added");
      onSaved();
      onOpenChange(false);
    } catch (error) {
      toast.error(errorText(error, "Couldn't save it."));
    } finally {
      setBusy(null);
    }
  };

  const noun = family === "services" ? "service" : family === "libraries" ? "library" : "notification";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{saved ? `Change ${saved.name}` : `Add a ${noun}`}</DialogTitle>
          <DialogDescription>
            {family === "services"
              ? "An app that downloads what is asked for. Requests go to the services in their order."
              : family === "libraries"
                ? "Where songs or music videos already are: what it holds gets a green check."
                : "Where clipseerr tells about requests."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          {!saved && (
            <div className="grid gap-2 sm:grid-cols-2">
              {types.types.map((item) => (
                <button key={item.type} type="button" onClick={() => pick(item.type)}
                        className={cn("rounded-lg border p-3 text-left transition",
                                      type === item.type ? "border-rose-400/60 bg-rose-400/10" : "border-border hover:bg-accent")}>
                  <p className="text-sm font-medium">{item.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{item.description}</p>
                </button>
              ))}
            </div>
          )}
          {kind && (
            <>
              {kind.homepage && (
                <a href={kind.homepage} target="_blank" rel="noreferrer" className="-mt-2 flex items-center gap-1 text-xs text-rose-300 hover:underline">
                  About {kind.label} <ExternalLink className="size-3" />
                </a>
              )}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="integration-name">Name</Label>
                <Input id="integration-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
              </div>
              {kind.fields.map((field) => (
                <FieldInput key={field.key} field={field} value={values[field.key]} secretSet={!!saved?.secrets_set.includes(field.key)}
                            onChange={(value) => setValues((old) => ({ ...old, [field.key]: value }))} />
              ))}
              {family === "notifications" && (
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
              )}
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="integration-enabled">On</Label>
                <Switch id="integration-enabled" checked={enabled} onCheckedChange={setEnabled} />
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
          <Button variant="secondary" onClick={test} disabled={!kind || !!busy}>
            {busy === "test" && <Loader2 className="animate-spin" />} {family === "notifications" ? "Send a test" : "Test"}
          </Button>
          <Button onClick={save} disabled={!kind || !!busy}>
            {busy === "save" && <Loader2 className="animate-spin" />} {saved ? "Save" : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
