import { Link } from "react-router";

import { Marquee } from "@/components/ui/marquee";
import type { AlbumSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

/** An album, single or EP in a row: its cover, its name, its kind and year. */
export function AlbumCard({ album, className }: { album: AlbumSummary; className?: string }) {
  return (
    <Link to={`/album/${album.id}`}
          className={cn("group flex min-w-0 flex-col gap-2 rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring", className)}>
      <div className="aspect-square overflow-hidden rounded-xl bg-secondary ring-1 ring-white/5">
        {album.thumbnail && <img src={album.thumbnail} alt="" loading="lazy" decoding="async"
                                 className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />}
      </div>
      <div className="min-w-0 px-0.5">
        <Marquee text={album.title} className="text-sm font-medium text-foreground/95" />
        <p className="text-xs text-muted-foreground">{[album.kind, album.year].filter(Boolean).join(" · ")}</p>
      </div>
    </Link>
  );
}
