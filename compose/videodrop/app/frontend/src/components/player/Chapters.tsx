import type { FileDetails } from "@/lib/api";
import { clock, cn } from "@/lib/utils";

/** A video's chapters, each one played from its start when clicked. */
export function Chapters({ chapters, onSeek, className }: {
  chapters: FileDetails["chapters"];
  onSeek: (time: number) => void;
  className?: string;
}) {
  return (
    <ol className={cn("scroll-thin flex max-h-48 flex-col overflow-y-auto", className)}>
      {chapters.map((chapter, n) => (
        <li key={chapter.start}>
          <button type="button" onClick={() => onSeek(chapter.start)}
                  className="flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none">
            <span className="w-12 shrink-0 text-xs text-muted-foreground tabular-nums">{clock(chapter.start)}</span>
            <span className="min-w-0 flex-1 truncate">{chapter.title || `Chapter ${n + 1}`}</span>
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{clock(chapter.end - chapter.start)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
