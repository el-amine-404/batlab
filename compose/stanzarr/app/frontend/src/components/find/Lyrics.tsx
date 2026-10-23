import { clock } from "@/lib/format";
import { cn } from "@/lib/utils";

const STAMP = /^\s*\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]\s?(.*)$/;

/** An answer's lyrics, in full (the page scrolls, not a box): synced lines with their times on one baseline, else the words. */
export function Lyrics({ text, synced, className }: { text: string; synced: boolean; className?: string }) {
  const lines = text.split("\n").filter((line, n, all) => line.trim() || (n > 0 && n < all.length - 1));
  return (
    <ol className={cn("text-[15px] leading-7", className)}>
      {lines.map((line, n) => {
        const stamp = synced ? STAMP.exec(line) : null;
        if (synced && !stamp && /^\s*\[[a-z#]+:/i.test(line)) return null;
        const at = stamp ? Number(stamp[1]) * 60 + Number(stamp[2].replace(":", ".")) : 0;
        const words = stamp ? stamp[3] : line;
        return (
          <li key={n} className={cn("grid items-baseline gap-3", synced ? "grid-cols-[3.75rem_1fr]" : "grid-cols-1",
                                    /^\[.*\]$/.test(words.trim()) && "pt-2 text-xs font-medium text-muted-foreground uppercase")}>
            {synced && <span className="font-mono text-[11px] text-muted-foreground/80 tabular-nums">{stamp ? clock(at).replace(/\.\d+$/, "") : ""}</span>}
            <span>{words || " "}</span>
          </li>
        );
      })}
    </ol>
  );
}
