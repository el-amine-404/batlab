import { Slider } from "radix-ui";
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { Section } from "@/lib/api";
import { clock, cn, parseClock } from "@/lib/utils";

/** A time typed as 1:30 (or 90): kept as text while it isn't one. */
function TimeField({ label, value, min, max, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (seconds: number) => void;
}) {
  const [text, setText] = useState(clock(value));
  useEffect(() => setText(clock(value)), [value]);
  const typed = parseClock(text);
  const bad = typed === null || typed < min || typed > max;
  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      {label}
      <Input value={text} inputMode="numeric" aria-invalid={bad || undefined} aria-label={`${label}, as minutes:seconds`}
             className={cn("h-8 w-24 text-center tabular-nums", bad && "border-destructive/60")}
             onChange={(event) => {
               setText(event.target.value);
               const seconds = parseClock(event.target.value);
               if (seconds !== null && seconds >= min && seconds <= max) onChange(seconds);
             }}
             onBlur={() => setText(clock(value))} />
    </label>
  );
}

/**
 * Only a part of the video: from, to. Cut at the keyframes around it, without
 * re-encoding, so it may start a few seconds early. null: the whole video.
 */
export function SectionPicker({ duration, value, onChange }: {
  duration: number;
  value: Section | null;
  onChange: (section: Section | null) => void;
}) {
  const whole = Math.floor(duration);
  const range: [number, number] = value ? [value.start, value.end] : [0, whole];
  const set = ([start, end]: number[]) => onChange({ start, end });
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border p-3">
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>
          Only a part of the video
          <span className="block text-xs text-muted-foreground">
            {value ? `${clock(value.end - value.start)} of ${clock(whole)}: cut at the nearest keyframes, it may start a few seconds early.` : `The whole ${clock(whole)}.`}
          </span>
        </span>
        <Switch checked={!!value} onCheckedChange={(on) => onChange(on ? { start: 0, end: whole } : null)} />
      </label>
      {value && (
        <>
          <Slider.Root min={0} max={whole} step={1} minStepsBetweenThumbs={1} value={range} onValueChange={set}
                       className="relative flex h-5 w-full touch-none items-center select-none">
            <Slider.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-secondary">
              <Slider.Range className="absolute h-full bg-gradient-to-r from-sky-500 to-cyan-400" />
            </Slider.Track>
            {["Start", "End"].map((name) => (
              <Slider.Thumb key={name} aria-label={name}
                            className="block size-4 rounded-full border-2 border-sky-300 bg-background shadow outline-none focus-visible:ring-[3px] focus-visible:ring-ring" />
            ))}
          </Slider.Root>
          <div className="flex flex-wrap items-center gap-3">
            <TimeField label="From" value={range[0]} min={0} max={range[1] - 1} onChange={(start) => set([start, range[1]])} />
            <TimeField label="To" value={range[1]} min={range[0] + 1} max={whole} onChange={(end) => set([range[0], end])} />
          </div>
        </>
      )}
    </div>
  );
}
