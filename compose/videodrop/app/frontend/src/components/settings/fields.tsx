import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/States";
import { Failed, Loading } from "@/components/layout/States";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Settings } from "@/lib/api";
import { type SettingsDraft, useSettingsDraft } from "@/lib/useSettingsDraft";
import { cn } from "@/lib/utils";

import { Unsaved } from "./Unsaved";

/** A group of settings on a card: what they are about, then the fields. */
export function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="font-medium">{title}</h2>
      {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-5 flex flex-col gap-5">{children}</div>
    </Card>
  );
}

type NumericKey = { [K in keyof Settings]: Settings[K] extends number ? K : never }[keyof Settings];

/** A number setting: typed freely, kept as a number once it is one. */
export function NumberField({ draft, name, label, help, min, max, step = 1, unit }: {
  draft: SettingsDraft;
  name: NumericKey;
  label: string;
  help: ReactNode;
  min: number;
  max: number;
  step?: number;
  unit?: string;
}) {
  const current = draft.value(name);
  const [text, setText] = useState(String(current ?? ""));
  // Discard, or another tab's save: the field follows.
  useEffect(() => setText(String(current ?? "")), [current]);
  const changed = draft.changed.includes(name);
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={name} className="flex items-center gap-2">
        {label}
        {changed && <span className="size-1.5 rounded-full bg-warning" title="Changed, not saved" />}
      </Label>
      <div className="flex items-center gap-2">
        <Input id={name} type="number" min={min} max={max} step={step} required value={text} className={cn("max-w-32", changed && "border-warning/60")}
               onChange={(e) => {
                 setText(e.target.value);
                 if (e.target.value !== "" && Number.isFinite(e.target.valueAsNumber)) draft.set(name, e.target.valueAsNumber);
               }} />
        {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
      </div>
      <p className="text-xs text-muted-foreground">{help}</p>
    </div>
  );
}

/**
 * A settings screen: its header, its form, the unsaved-changes bar and the
 * question before leaving with changes. children gets the draft.
 */
export function SettingsScreen({ title, description, children }: {
  title: string;
  description: string;
  children: (draft: SettingsDraft) => ReactNode;
}) {
  const draft = useSettingsDraft();
  if (draft.isPending) return <Loading />;
  if (draft.error || !draft.saved) return <Failed error={draft.error} retry={() => void draft.refetch()} />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void draft.save();
  };
  return (
    <>
      <Unsaved dirty={draft.dirty} busy={draft.busy} onSave={draft.save} onDiscard={draft.discard} />
      <PageHeader eyebrow="Settings" title={title} description={description} />
      <form onSubmit={submit} className="flex flex-col gap-4">
        {children(draft)}
        {/* Enter in a field saves, like the bar's button. */}
        <button type="submit" hidden />
      </form>
    </>
  );
}
