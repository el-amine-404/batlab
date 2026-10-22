import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Rows that belong together, under an optional heading. */
export function SettingGroup({ title, description, children }: { title?: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section>
      {title && <h2 className="mb-1 text-sm font-semibold">{title}</h2>}
      {description && <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{description}</p>}
      <Card className="divide-y divide-border p-0">{children}</Card>
    </section>
  );
}

/** One setting: its name on the left, the control and what it does on the right. */
export function SettingRow({
  label,
  htmlFor,
  help,
  children,
}: {
  label: string;
  htmlFor?: string;
  help?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-2 px-4 py-4 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-6">
      <label htmlFor={htmlFor} className="pt-2 text-sm font-medium">
        {label}
      </label>
      <div className="min-w-0">
        {children}
        {help && <div className="mt-2 text-xs leading-relaxed text-muted-foreground">{help}</div>}
      </div>
    </div>
  );
}

/** A whole number within the API's limits, with its unit. */
export function NumberSetting({
  id,
  label,
  unit,
  help,
  value,
  range,
  onChange,
}: {
  id: string;
  label: string;
  unit: string;
  help: ReactNode;
  value: number;
  range: number[];
  onChange: (value: number) => void;
}) {
  const [low, high] = range;
  const invalid = !Number.isInteger(value) || value < low || value > high;
  return (
    <SettingRow label={label} htmlFor={id} help={invalid ? <span className="text-destructive">Between {low} and {high}.</span> : help}>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={low}
          max={high}
          value={Number.isNaN(value) ? "" : value}
          onChange={(event) => onChange(event.target.valueAsNumber)}
          aria-invalid={invalid}
          className={cn("h-9 w-28 tabular-nums", invalid && "border-destructive")}
        />
        <span className="text-sm text-muted-foreground">{unit}</span>
      </div>
    </SettingRow>
  );
}

export const isWhole = (value: number, range: number[]) => Number.isInteger(value) && value >= range[0] && value <= range[1];
