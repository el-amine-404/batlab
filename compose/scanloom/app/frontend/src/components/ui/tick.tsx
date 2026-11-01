import { Check } from "lucide-react";
import type { MouseEvent } from "react";

import { cn } from "@/lib/utils";

/** The small box on a card or a row that selects it; a generous target around it for a finger. */
export function Tick({ checked, label, onToggle, className }: { checked: boolean; label: string; onToggle: (event: MouseEvent) => void; className?: string }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} title={label}
            onClick={(event) => { event.preventDefault(); event.stopPropagation(); onToggle(event); }}
            className={cn("group/tick relative z-10 flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg outline-none focus-visible:ring-[3px] focus-visible:ring-ring", className)}>
      <span className={cn("flex size-5 items-center justify-center rounded border-2 transition-colors", checked ? "border-primary bg-primary text-primary-foreground" : "border-white/80 bg-black/55 group-hover/tick:border-white group-hover/tick:bg-black/80")}>
        {checked && <Check className="size-3.5" strokeWidth={3} />}
      </span>
    </button>
  );
}
