import { ChevronRight, Loader2 } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import type { SongRow } from "@/lib/api";
import { FORMAT, length } from "@/lib/format";
import { cn } from "@/lib/utils";

import { StatusBadge } from "./StatusBadge";

/** The songs as a list: each one a link to its page; "Show more" while there are more. */
export function SongRows({ songs, total, more, loadingMore, onMore }: {
  songs: SongRow[];
  total: number;
  more: boolean;
  loadingMore: boolean;
  onMore: () => void;
}) {
  return (
    <>
      <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
        {songs.map((song) => (
          <li key={song.id}>
            <Link to={`/songs/${song.id}`}
                  className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/60 focus-visible:bg-accent/60 focus-visible:outline-none">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{song.title}</p>
                <p className="truncate text-xs text-muted-foreground">{song.artist}{song.album ? ` · ${song.album}` : ""}</p>
              </div>
              <div className="hidden w-40 shrink-0 flex-col items-start gap-0.5 sm:flex">
                <StatusBadge status={song.status} />
                {!!song.to_check && song.to_check > 0 && (
                  <span className="text-[11px] text-warning">{song.to_check} lines to check</span>
                )}
                {(song.job === "queued" || song.job === "running") && (
                  <span className="text-[11px] text-primary">AI sync {song.job}</span>
                )}
              </div>
              <div className="hidden w-32 shrink-0 flex-wrap gap-1 md:flex">
                {song.formats.map((format) => (
                  <span key={format} className="rounded border border-border px-1.5 text-[10px] font-medium text-muted-foreground uppercase">
                    {FORMAT[format] ?? format}
                  </span>
                ))}
              </div>
              <span className="hidden w-20 shrink-0 truncate text-xs text-muted-foreground lg:block">
                {song.languages.map((l) => l.name).join(", ")}
              </span>
              <span className="w-11 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{length(song.duration)}</span>
              <StatusBadge status={song.status} className="sm:hidden" />
              <ChevronRight className={cn("size-4 shrink-0 text-muted-foreground/50 transition group-hover:text-foreground")} />
            </Link>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="tabular-nums">{songs.length.toLocaleString()} of {total.toLocaleString()}</span>
        {more && (
          <Button variant="secondary" size="sm" onClick={onMore} disabled={loadingMore}>
            {loadingMore && <Loader2 className="animate-spin" />} Show more
          </Button>
        )}
      </div>
    </>
  );
}
