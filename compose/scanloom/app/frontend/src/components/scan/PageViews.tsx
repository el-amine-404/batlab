import { Crop, Download, Loader2, MoreVertical, RotateCw, Trash2, TriangleAlert } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tick } from "@/components/ui/tick";
import { Menu, MenuContent, MenuItem, MenuLink, MenuTrigger } from "@/components/ui/menu";
import type { Page } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";

import { STAGE } from "./labels";

export type PageActions = {
  open: (page: Page) => void;
  rotate: (page: Page) => void;
  crop: (page: Page) => void;
  remove: (page: Page) => void;
};

const working = (page: Page) => !["ready", "failed", "blocked"].includes(page.status);

/** The picture of a page, or what is happening to it until there is one. */
function Picture({ page, number, className }: { page: Page; number: number; className?: string }) {
  if (page.thumb_url) {
    return (
      <>
        <img src={page.thumb_url} alt={`Page ${number}`} loading="lazy" decoding="async" draggable={false}
             className={cn("mx-auto size-full object-contain drop-shadow-md", working(page) && "opacity-50", className)} />
        {working(page) && <span className="absolute inset-0 flex items-center justify-center"><Loader2 className="size-6 animate-spin text-foreground" aria-label="Updating" /></span>}
      </>
    );
  }
  if (page.status === "failed" || page.status === "blocked") {
    return (
      <span className="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs text-destructive">
        <TriangleAlert className="size-5" /> {STAGE[page.status]}
      </span>
    );
  }
  return (
    <span className="flex size-full flex-col items-center justify-center gap-2 p-2 text-center text-xs text-muted-foreground">
      <Loader2 className={cn("size-5", page.status !== "queued" && "animate-spin")} aria-hidden /> {STAGE[page.status]}
    </span>
  );
}

function More({ page, actions, trigger }: { page: Page; actions: PageActions; trigger: ReactNode }) {
  return (
    <Menu>
      <MenuTrigger asChild>{trigger}</MenuTrigger>
      <MenuContent onClick={(event: MouseEvent) => event.stopPropagation()}>
        {page.png_url && <MenuLink href={page.png_url} download><Download /> Lossless PNG{page.result_bytes ? ` · ${formatBytes(page.result_bytes)}` : ""}</MenuLink>}
        {page.jpeg_url && <MenuLink href={page.jpeg_url} download><Download /> JPEG{page.jpeg_bytes ? ` · ${formatBytes(page.jpeg_bytes)}` : ""}</MenuLink>}
        <MenuItem disabled={!page.source_url} onSelect={() => actions.crop(page)}><Crop /> Adjust crop</MenuItem>
        <MenuItem danger onSelect={() => actions.remove(page)}><Trash2 /> Delete page</MenuItem>
      </MenuContent>
    </Menu>
  );
}

const onImage = "flex size-9 items-center justify-center rounded-lg bg-black/75 text-white transition-colors hover:bg-black focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none disabled:opacity-40 [&_svg]:size-4";

export function PageTile({ page, number, actions, selected, onSelect }: {
  page: Page; number: number; actions: PageActions; selected: boolean; onSelect: () => void;
}) {
  const notes = page.warnings.length;
  const stop = (event: MouseEvent) => event.stopPropagation();
  return (
    <article className={cn("group relative overflow-hidden rounded-xl border bg-card transition-colors", selected ? "border-primary ring-2 ring-primary/50" : "border-border hover:border-foreground/30")}>
      <button type="button" onClick={() => actions.open(page)} aria-label={`Open page ${number}, ${page.original_name}`}
              className="relative block aspect-[3/4] w-full bg-tile p-2 outline-none focus-visible:ring-[3px] focus-visible:ring-inset focus-visible:ring-ring">
        <Picture page={page} number={number} />
      </button>
      <Tick checked={selected} label={`Select page ${number}`} onToggle={onSelect} className="absolute top-1 left-1" />
      <div className="absolute top-2 right-2 flex gap-1 opacity-100 transition-opacity [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-focus-within:opacity-100 [@media(hover:hover)]:group-hover:opacity-100" onClick={stop}>
        <button type="button" className={cn(onImage, "max-[28rem]:hidden")} disabled={working(page)} title="Rotate clockwise" aria-label={`Rotate page ${number} clockwise`} onClick={() => actions.rotate(page)}><RotateCw /></button>
        <More page={page} actions={actions} trigger={<button type="button" className={onImage} aria-label={`More for page ${number}`}><MoreVertical /></button>} />
      </div>
      <div className="flex items-center gap-2 border-t border-border px-2.5 py-2 text-sm">
        <span className="font-semibold tabular-nums">{number}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={page.original_name}>{page.original_name}</span>
        {page.status === "failed" || page.status === "blocked" ? <TriangleAlert className="size-4 shrink-0 text-destructive" aria-label={STAGE[page.status]} />
          : notes > 0 && <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-warning" title={page.warnings.join("\n")}><TriangleAlert className="size-3.5" aria-hidden /><span className="sr-only">Quality notes:</span>{notes}</span>}
      </div>
    </article>
  );
}

export function PageRow({ page, number, actions, selected, onSelect }: {
  page: Page; number: number; actions: PageActions; selected: boolean; onSelect: () => void;
}) {
  const size = page.width && page.height ? `${page.width} × ${page.height}` : null;
  return (
    <li className={cn("flex items-center gap-3 px-3 py-2 transition-colors hover:bg-accent/50", selected && "bg-primary/10")}>
      <Tick checked={selected} label={`Select page ${number}`} onToggle={onSelect} className="[&>span]:border-muted-foreground" />
      <span className="w-7 shrink-0 text-right text-sm font-semibold tabular-nums">{number}</span>
      <button type="button" onClick={() => actions.open(page)} aria-label={`Open page ${number}`}
              className="relative size-14 shrink-0 overflow-hidden rounded-md bg-tile p-0.5 outline-none focus-visible:ring-[3px] focus-visible:ring-ring">
        <Picture page={page} number={number} />
      </button>
      <button type="button" onClick={() => actions.open(page)} className="min-w-0 flex-1 rounded text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring">
        <span className="block truncate text-sm font-medium">{page.original_name}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {page.status === "failed" || page.status === "blocked" ? page.error ?? STAGE[page.status]
            : page.status !== "ready" ? STAGE[page.status]
            : [size, page.result_bytes && formatBytes(page.result_bytes)].filter(Boolean).join(" · ")}
        </span>
      </button>
      {page.warnings.length > 0 && <Badge variant="warning" className="max-sm:hidden" title={page.warnings.join("\n")}><TriangleAlert /> {page.warnings.length === 1 ? "1 note" : `${page.warnings.length} notes`}</Badge>}
      <div className="flex shrink-0 items-center gap-0.5">
        <Button variant="ghost" size="icon" className="max-sm:hidden" disabled={working(page)} aria-label={`Rotate page ${number} clockwise`} title="Rotate clockwise" onClick={() => actions.rotate(page)}><RotateCw /></Button>
        <Button variant="ghost" size="icon" className="max-sm:hidden" disabled={!page.source_url} aria-label={`Adjust the crop of page ${number}`} title="Adjust crop" onClick={() => actions.crop(page)}><Crop /></Button>
        <More page={page} actions={actions} trigger={<Button variant="ghost" size="icon" aria-label={`More for page ${number}`}><MoreVertical /></Button>} />
      </div>
    </li>
  );
}
