import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const ALL = 1000;

/** Which stretch of a long list is on screen, and how much of it: up to all of it. Fixed to the screen's bottom, so it never moves with the content; a page using it leaves room with pb-16. */
export function Pagination({ total, size, sizes, offset, noun, onChange }: {
  total: number; size: number; sizes: number[]; offset: number; noun: string;
  onChange: (next: { size: number; offset: number }) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const page = Math.floor(offset / size) + 1;
  const to = (target: number) => onChange({ size, offset: (target - 1) * size });
  return (
    <nav aria-label={`${noun} pages`} className="fixed right-0 bottom-0 left-0 z-30 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border bg-background/95 px-4 py-2 pb-[max(.5rem,env(safe-area-inset-bottom))] text-sm text-muted-foreground backdrop-blur sm:px-6 lg:left-64 lg:px-10">
      <p className="tabular-nums" aria-live="polite">{total ? `${offset + 1}–${Math.min(total, offset + size)} of ${total}` : `No ${noun}`}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={String(size)} onValueChange={(value) => onChange({ size: Number(value), offset: 0 })}>
          <SelectTrigger aria-label={`${noun} on a page`} className="h-9 w-auto gap-2"><SelectValue /></SelectTrigger>
          <SelectContent>
            {sizes.map((choice) => <SelectItem key={choice} value={String(choice)}>{choice} a page</SelectItem>)}
            <SelectItem value={String(ALL)}>Show all</SelectItem>
          </SelectContent>
        </Select>
        {pages > 1 && (
          <div className="flex items-center gap-1">
            <Button variant="secondary" size="icon" aria-label="Previous" disabled={page <= 1} onClick={() => to(page - 1)}><ChevronLeft /></Button>
            <span className="min-w-16 text-center tabular-nums">{page} / {pages}</span>
            <Button variant="secondary" size="icon" aria-label="Next" disabled={page >= pages} onClick={() => to(page + 1)}><ChevronRight /></Button>
          </div>
        )}
      </div>
    </nav>
  );
}
