import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const ALL = 1000;

/** Which stretch of a long list is on screen, and how much of it: up to all of it. */
export function Pagination({ total, size, sizes, offset, noun, onChange }: {
  total: number; size: number; sizes: number[]; offset: number; noun: string;
  onChange: (next: { size: number; offset: number }) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const page = Math.floor(offset / size) + 1;
  const to = (target: number) => onChange({ size, offset: (target - 1) * size });
  return (
    <nav aria-label={`${noun} pages`} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm text-muted-foreground">
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
