import { cn } from "@/lib/utils"
import type { Line } from "./Timeline"

/** Word start times inside a line: the line's time spread over its words by
 *  length, up to the next line (at most 0.45 s per character-weighted word).
 *  An estimate: the .lrc holds line times only. */
function words(line: Line, next: Line | undefined) {
  const parts = line.s.split(/(\s+)/).filter((p) => p.trim())
  const weights = parts.map((w) => w.length + 2)
  const total = weights.reduce((a, b) => a + b, 0)
  const end = Math.min(next ? next.t : line.t + 4, line.t + total * 0.09)
  let at = line.t
  return parts.map((w, i) => {
    const start = at
    at += ((end - line.t) * weights[i]) / total
    return { w, start }
  })
}

/** The lyrics as a player shows them: the line being sung, word by word,
 *  between the one before and the one after. */
export function Stage({ lines, current, position }: { lines: Line[]; current: number; position: number }) {
  const line = lines[current]
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 px-8 text-center">
      <p className="h-6 truncate text-sm text-muted-foreground/60">{lines[current - 1]?.s ?? ""}</p>
      <p className="text-3xl leading-tight font-semibold tracking-tight">
        {line ? words(line, lines[current + 1]).map(({ w, start }, i) => (
          <span key={i} className={cn("transition-colors duration-150", start <= position ? "text-foreground" : "text-muted-foreground/50")}>
            {w}{" "}
          </span>
        )) : <span className="text-muted-foreground/50">♪</span>}
      </p>
      <p className="h-6 truncate text-sm text-muted-foreground/60">{lines[current + 1]?.s ?? ""}</p>
    </div>
  )
}
