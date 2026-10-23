import { clock } from "@/lib/format";
import { cn } from "@/lib/utils";

const STAMP = /^\s*\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s?(.*)$/;

/** An answer's lyrics, read here: synced lines with their times on one baseline, else the words. */
export function Lyrics({ text, synced, className }: { text: string; synced: boolean; className?: string }) {
  const lines = text.split("\n").filter((line, n, all) => line.trim() || (n > 0 && n < all.length - 1));
  return (
    <ol className={cn("max-h-80 overflow-y-auto rounded-lg bg-background/50 p-2 text-sm leading-6", className)}>
      {lines.map((line, n) => {
        const stamp = synced ? STAMP.exec(line) : null;
        if (synced && !stamp && /^\s*\[[a-z#]+:/i.test(line)) return null;
        const at = stamp ? Number(stamp[1]) * 60 + Number(stamp[2].replace(":", ".")) : 0;
        return (
          <li key={n} className={cn("grid items-baseline gap-3", synced ? "grid-cols-[5.5rem_1fr]" : "grid-cols-1")}>
            {synced && <span className="font-mono text-xs text-muted-foreground tabular-nums">{stamp ? clock(at) : ""}</span>}
            <span>{(stamp ? stamp[3] : line) || " "}</span>
          </li>
        );
      })}
    </ol>
  );
}
