import { ArrowDownToLine, ChevronRight, Download, Search } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { CopyButton } from "@/components/common/CopyButton";
import { Button } from "@/components/ui/button";
import type { LogLevel } from "@/lib/api";
import { cn } from "@/lib/utils";

export type LogLine = { seq: number; at: number; level: LogLevel; source: string; message: string; detail?: string | null };

const LEVELS: { value: LogLevel; label: string }[] = [
  { value: "debug", label: "All" },
  { value: "info", label: "Info" },
  { value: "warning", label: "Warnings" },
  { value: "error", label: "Errors" },
];
const ORDER: Record<LogLevel, number> = { debug: 0, info: 1, warning: 2, error: 3 };
const TONE: Record<LogLevel, string> = {
  debug: "text-muted-foreground/70",
  info: "text-sky-300",
  warning: "text-warning",
  error: "text-destructive",
};

const stamp = (at: number) => {
  const date = new Date(at * 1000);
  return `${date.toLocaleTimeString(undefined, { hour12: false })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
};

/** A line as text, for copying: its date and time in UTC, like the server's own log. */
const asText = (line: LogLine) =>
  `${new Date(line.at * 1000).toISOString()} ${line.level.toUpperCase().padEnd(7)} ${line.source} ${line.message}${line.detail ? `\n${line.detail}` : ""}`;

function Line({ line }: { line: LogLine }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={cn("grid grid-cols-[auto_1fr] gap-x-2 px-3 py-0.5 sm:grid-cols-[auto_auto_1fr] hover:bg-white/[0.03]", line.level === "error" && "bg-destructive/5")}>
      <time className="text-muted-foreground/70 tabular-nums" dateTime={new Date(line.at * 1000).toISOString()}
            title={new Date(line.at * 1000).toLocaleString()}>{stamp(line.at)}</time>
      <span className={cn("w-12 uppercase", TONE[line.level])}>{line.level === "warning" ? "warn" : line.level}</span>
      <span className="col-span-2 min-w-0 break-words whitespace-pre-wrap sm:col-span-1">
        <span className="mr-1.5 text-muted-foreground">{line.source}</span>
        {line.message}
        {line.detail && (
          <>
            <button type="button" onClick={() => setOpen(!open)} className="ml-1.5 inline-flex items-center text-muted-foreground hover:text-foreground">
              <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />traceback
            </button>
            {open && <pre className="mt-1 overflow-x-auto rounded bg-black/40 p-2 text-[11px] text-muted-foreground">{line.detail}</pre>}
          </>
        )}
      </span>
    </li>
  );
}

/**
 * A log to read: its lines (time, level, who said it, what), filtered by
 * level and by words, kept at the bottom while new ones come (unless
 * scrolled up), copied or downloaded as text.
 */
export function LogView({ lines, loading, error, file, initialLevel = "info", className, height = "h-80" }: {
  lines: LogLine[];
  loading: boolean;
  error: unknown;
  /** Where the whole log downloads from; else the lines shown are saved. */
  file?: { url: string; name: string };
  initialLevel?: LogLevel;
  className?: string;
  /** The lines' height, a class. */
  height?: string;
}) {
  const [level, setLevel] = useState<LogLevel>(initialLevel);
  const [words, setWords] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  const shown = useMemo(() => {
    const needle = words.trim().toLowerCase();
    return lines.filter((line) => ORDER[line.level] >= ORDER[level]
      && (!needle || `${line.source} ${line.message}`.toLowerCase().includes(needle)));
  }, [lines, level, words]);
  const counts = useMemo(() => ({
    warning: lines.filter((line) => line.level === "warning").length,
    error: lines.filter((line) => line.level === "error").length,
  }), [lines]);

  useLayoutEffect(() => {
    if (pinned && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown, pinned]);

  const text = () => shown.map(asText).join("\n");
  const save = () => {
    const url = URL.createObjectURL(new Blob([`${text()}\n`], { type: "text/plain" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: "videodrop.log" });
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className={cn("flex min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-black/30", className)}>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-2">
        <div className="flex rounded-md bg-secondary p-0.5 text-xs" role="radiogroup" aria-label="Which lines">
          {LEVELS.map((item) => (
            <button key={item.value} type="button" role="radio" aria-checked={level === item.value} onClick={() => setLevel(item.value)}
                    className={cn("rounded px-2 py-1 text-muted-foreground transition-colors hover:text-foreground",
                                  level === item.value && "bg-background text-foreground shadow-sm")}>
              {item.label}
              {item.value === "warning" && counts.warning > 0 && <span className="ml-1 text-warning">{counts.warning}</span>}
              {item.value === "error" && counts.error > 0 && <span className="ml-1 text-destructive">{counts.error}</span>}
            </button>
          ))}
        </div>
        <label className="flex min-w-32 flex-1 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-xs">
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
          <input value={words} onChange={(event) => setWords(event.target.value)} placeholder="Find in the log"
                 className="h-7 min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground" />
        </label>
        <CopyButton text={text} label="lines shown" />
        {file ? (
          <Button variant="ghost" size="icon-sm" asChild title="Download the whole log">
            <a href={file.url} download={file.name} aria-label="Download the whole log"><Download /></a>
          </Button>
        ) : (
          <Button variant="ghost" size="icon-sm" title="Download the lines shown" aria-label="Download the lines shown" onClick={save}>
            <Download />
          </Button>
        )}
      </div>
      <div className="relative">
        <div ref={box} className={cn("scroll-thin overflow-y-auto py-1.5 font-mono text-xs leading-relaxed", height)}
             onScroll={(event) => {
               const el = event.currentTarget;
               setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
             }}>
          {error ? (
            <p className="px-3 py-2 text-destructive">The log couldn't be read.</p>
          ) : loading && !lines.length ? (
            <p className="px-3 py-2 text-muted-foreground">Reading…</p>
          ) : !shown.length ? (
            <p className="px-3 py-2 text-muted-foreground">{lines.length ? "No line matches." : "Nothing logged yet."}</p>
          ) : (
            <ol>{shown.map((line) => <Line key={line.seq} line={line} />)}</ol>
          )}
        </div>
        {!pinned && (
          <Button size="sm" variant="secondary" className="absolute right-3 bottom-3 shadow-lg"
                  onClick={() => box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" })}>
            <ArrowDownToLine /> Newest
          </Button>
        )}
      </div>
    </div>
  );
}
