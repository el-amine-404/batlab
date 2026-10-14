import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { Field, OptionValues } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  title: string;
  fields: Field[];
  values: OptionValues;
  onChange: (values: OptionValues) => void;
  onReset: () => void;
  /** Inside another card: no frame of its own. */
  plain?: boolean;
};

function Flag({ children }: { children: string }) {
  return <code className="rounded bg-white/6 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{children}</code>;
}

export function OptionsPanel({ title, fields, values, onChange, onReset, plain = false }: Props) {
  const selects = fields.filter((field) => field.type === "select");
  const switches = fields.filter((field) => field.type === "switch");
  const times = fields.filter((field) => field.type === "time");
  const set = (key: string, value: string | boolean) => onChange({ ...values, [key]: value });

  return (
    <div className={cn(plain ? "pt-4" : "mt-4 rounded-lg border border-border bg-black/15 p-4")}>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm font-medium">{title} options</p>
        <Button type="button" variant="ghost" size="sm" onClick={onReset}>
          <RotateCcw /> Defaults
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {selects.map((field) => (
          <div key={field.key} className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor={`option-${field.key}`}>{field.label}</Label>
              <Flag>{field.flag}</Flag>
            </div>
            <Select value={String(values[field.key])} onValueChange={(value) => set(field.key, value)}>
              <SelectTrigger id={`option-${field.key}`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {field.choices?.map((choice) => (
                  <SelectItem key={choice.value} value={choice.value}>
                    {choice.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs leading-relaxed text-muted-foreground">{field.help}</p>
          </div>
        ))}
      </div>

      {times.length > 0 && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {times.map((field) => (
            <div key={field.key} className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor={`option-${field.key}`}>{field.label}</Label>
                <Flag>{field.flag}</Flag>
              </div>
              <Input
                id={`option-${field.key}`}
                value={String(values[field.key] ?? "")}
                onChange={(event) => set(field.key, event.target.value)}
                placeholder="m:ss"
                inputMode="numeric"
                autoComplete="off"
                pattern="[0-9:]*"
                maxLength={8}
              />
              <p className="text-xs leading-relaxed text-muted-foreground">{field.help}</p>
            </div>
          ))}
        </div>
      )}

      {switches.length > 0 && (
        <div className="mt-5 divide-y divide-border border-t border-border">
          {switches.map((field) => (
            <div key={field.key} className="flex items-start justify-between gap-4 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Label htmlFor={`option-${field.key}`}>{field.label}</Label>
                  <Flag>{field.flag}</Flag>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{field.help}</p>
              </div>
              <Switch
                id={`option-${field.key}`}
                className="mt-0.5"
                checked={Boolean(values[field.key])}
                onCheckedChange={(checked) => set(field.key, checked)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
