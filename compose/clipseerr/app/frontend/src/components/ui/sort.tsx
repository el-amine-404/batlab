import { ArrowDownAZ, CalendarArrowDown, Eye, History, type LucideIcon, Sparkles } from "lucide-react";

import { Hint } from "@/components/ui/hint";
import { cn } from "@/lib/utils";

export type SortOption<T extends string> = { value: T; icon: LucideIcon; label: string };

/** The list's orders, as icons with a hint each; the one in use is lit. */
export function SortControl<T extends string>({ options, value, onChange }: {
  options: SortOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="Order">
      {options.map((option) => (
        <Hint key={option.value} text={option.label}>
          <button type="button" aria-label={option.label} aria-pressed={value === option.value} onClick={() => onChange(option.value)}
                  className={cn("flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground",
                                value === option.value && "bg-accent text-foreground")}>
            <option.icon className="size-4" />
          </button>
        </Hint>
      ))}
    </div>
  );
}

export type ListSort = "default" | "newest" | "views" | "title";

/** The orders of a list: its own first (a chart's rank, a search's relevance), named for it. */
export function listSorts(own: string, ownIcon: LucideIcon = Sparkles): SortOption<ListSort>[] {
  return [
    { value: "default", icon: ownIcon, label: own },
    { value: "newest", icon: CalendarArrowDown, label: "Newest out" },
    { value: "views", icon: Eye, label: "Most viewed" },
    { value: "title", icon: ArrowDownAZ, label: "A to Z" },
  ];
}

export { History };
