import { type PointerEvent, useRef, useState } from "react";

import type { FileDetails } from "@/lib/api";
import { local } from "@/lib/api";
import { clock, cn } from "@/lib/utils";

type Board = NonNullable<FileDetails["storyboard"]>;

/** What the video looks like at a moment: its storyboard picture, scaled. */
function Still({ board, time, scale }: { board: Board; time: number; scale: number }) {
  const n = Math.min(board.count - 1, Math.max(0, Math.floor(time / board.every)));
  const rows = Math.ceil(board.count / board.columns);
  const w = board.width * scale;
  const h = board.height * scale;
  return (
    <div className="overflow-hidden rounded-md border border-white/20 bg-black shadow-lg shadow-black/60"
         style={{
           width: w, height: h,
           backgroundImage: `url("${local(board.url)}")`,
           backgroundSize: `${board.columns * w}px ${rows * h}px`,
           backgroundPosition: `-${(n % board.columns) * w}px -${Math.floor(n / board.columns) * h}px`,
         }} />
  );
}

/**
 * The bar to seek with: what is played and loaded, the chapters, and on hover
 * (or while dragging) the moment under the pointer: its picture, its time,
 * its chapter. The played part is drawn by the player (played.current), not
 * by React: it moves every frame.
 */
export function SeekBar({ duration, buffered, details, played, onSeek, onScrub, compact }: {
  duration: number;
  buffered: [number, number][];
  details?: FileDetails;
  played: React.RefObject<HTMLDivElement | null>;
  onSeek: (time: number) => void;
  /** While dragging: the time under the pointer, or null once let go. */
  onScrub: (time: number | null) => void;
  compact: boolean;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ x: number; time: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  const at = (event: PointerEvent) => {
    const box = bar.current!.getBoundingClientRect();
    const x = Math.min(box.width, Math.max(0, event.clientX - box.left));
    return { x, time: duration ? (x / box.width) * duration : 0, width: box.width };
  };
  const chapters = (details?.chapters ?? []).filter((c) => c.end > c.start);
  const chapter = hover ? chapters.find((c) => hover.time >= c.start && hover.time < c.end) : undefined;
  const board = details?.storyboard;
  const scale = compact ? 1 : 1.25;
  const previewWidth = board ? board.width * scale : 80;
  const width = bar.current?.clientWidth ?? 0;
  const left = hover ? Math.min(Math.max(hover.x - previewWidth / 2, 0), Math.max(0, width - previewWidth)) : 0;

  return (
    <div className="group/seek relative flex h-5 cursor-pointer touch-none items-center" ref={bar}
         role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={Math.round(duration)} tabIndex={-1}
         onPointerMove={(event) => {
           const point = at(event);
           setHover(point);
           if (dragging) onScrub(point.time);
         }}
         onPointerLeave={() => !dragging && setHover(null)}
         onPointerDown={(event) => {
           event.currentTarget.setPointerCapture(event.pointerId);
           setDragging(true);
           const point = at(event);
           setHover(point);
           onScrub(point.time);
         }}
         onPointerUp={(event) => {
           if (!dragging) return;
           setDragging(false);
           onScrub(null);
           onSeek(at(event).time);
           if (event.pointerType !== "mouse") setHover(null);
         }}>
      {hover && duration > 0 && (
        <div className="pointer-events-none absolute bottom-6 flex flex-col items-center gap-1" style={{ left, width: previewWidth }}>
          {board && <Still board={board} time={hover.time} scale={scale} />}
          <span className="max-w-full truncate rounded bg-black/80 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">
            {chapter?.title ? `${chapter.title} · ` : ""}{clock(hover.time)}
          </span>
        </div>
      )}
      <div className={cn("relative w-full overflow-hidden rounded-full bg-white/20 transition-[height]", dragging ? "h-1.5" : "h-1 group-hover/seek:h-1.5")}>
        {buffered.map(([start, end], n) => (
          <div key={n} className="absolute inset-y-0 bg-white/30"
               style={{ left: `${(start / duration) * 100}%`, width: `${((end - start) / duration) * 100}%` }} />
        ))}
        {hover && <div className="absolute inset-y-0 left-0 bg-white/25" style={{ width: hover.x }} />}
        <div ref={played} className="absolute inset-y-0 left-0 bg-gradient-to-r from-sky-500 to-cyan-400" style={{ width: 0 }} />
        {/* Chapters: a gap where each one starts. */}
        {duration > 0 && chapters.slice(1).map((c) => (
          <div key={c.start} className="absolute inset-y-0 w-0.5 bg-black/70" style={{ left: `${(c.start / duration) * 100}%` }} />
        ))}
      </div>
    </div>
  );
}
