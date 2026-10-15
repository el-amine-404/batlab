import { Plus, X } from "lucide-react";
import { type FormEvent, useState } from "react";

import { SettingGroup } from "@/components/settings/rows";
import { SettingsScreen } from "@/components/settings/SettingsScreen";
import { useSettingsForm } from "@/components/settings/useSettingsForm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { duration } from "@/lib/utils";

const KEYS = ["retry_minutes"] as const;

export function RetriesPage() {
  const form = useSettingsForm(KEYS);
  const { saved, draft, set } = form;

  return (
    <SettingsScreen
      title="Retries"
      description="A song that fails (YouTube refusing for a while, a video not found) is tried again on its own, this long after each failure. With none, only Retry now on the Missing screen tries again."
      form={form}
      ready={!!draft}
    >
      {saved && draft && (
        <SettingGroup>
          <Schedule
            value={draft.retry_minutes}
            max={saved.limits.retries as number}
            range={saved.limits.retry_minutes as number[]}
            onChange={(value) => set("retry_minutes", value)}
          />
        </SettingGroup>
      )}
    </SettingsScreen>
  );
}

/** The tries in order: the download itself, then one per delay. */
function Schedule({ value, max, range, onChange }: { value: number[]; max: number; range: number[]; onChange: (value: number[]) => void }) {
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState<"min" | "h">("min");
  const minutes = Math.round(Number(amount) * (unit === "h" ? 60 : 1));
  const valid = amount !== "" && minutes >= range[0] && minutes <= range[1];

  const add = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    onChange([...value, minutes]);
    setAmount("");
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <ol className="flex flex-col gap-2">
        <li className="flex items-center gap-3 text-sm">
          <span className="flex size-7 items-center justify-center rounded-full bg-accent text-xs tabular-nums">1</span>
          The download
        </li>
        {value.map((delay, position) => (
          <li key={position} className="flex items-center gap-3 text-sm">
            <span className="flex size-7 items-center justify-center rounded-full bg-primary/20 text-xs tabular-nums text-violet-200">{position + 2}</span>
            <span className="flex-1">{duration(delay)} after the last failure</span>
            <Button variant="ghost" size="icon-sm" aria-label={`Remove try ${position + 2}`} onClick={() => onChange(value.filter((_, at) => at !== position))}>
              <X />
            </Button>
          </li>
        ))}
      </ol>
      {value.length < max ? (
        <form className="flex flex-wrap items-center gap-2 border-t border-border pt-4" onSubmit={add}>
          <span className="text-sm text-muted-foreground">Add a try</span>
          <Input type="number" inputMode="decimal" min={0} value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="30" className="h-9 w-24" aria-label="After" />
          <select value={unit} onChange={(event) => setUnit(event.target.value as "min" | "h")} className="h-9 rounded-lg border border-input bg-black/20 px-2 text-sm" aria-label="Unit">
            <option value="min">minutes</option>
            <option value="h">hours</option>
          </select>
          <span className="text-sm text-muted-foreground">after the last failure</span>
          <Button type="submit" variant="secondary" size="sm" disabled={!valid}>
            <Plus /> Add
          </Button>
        </form>
      ) : (
        <p className="border-t border-border pt-4 text-xs text-muted-foreground">At most {max} later tries.</p>
      )}
    </div>
  );
}
