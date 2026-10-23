import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Disc3 } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, type SongRow, type SortKey } from "@/lib/api";
import { FORMAT, length, SOURCE, STATUS } from "@/lib/format";
import { cn, timeAgo } from "@/lib/utils";

import { StatusBadge } from "./StatusBadge";

export type Look = { compact: boolean; grouped: boolean };

const COLUMNS: { key: SortKey | null; label: string; className: string }[] = [
  { key: "title", label: "Title", className: "min-w-0 flex-[2]" },
  { key: "artist", label: "Artist", className: "hidden min-w-0 flex-1 md:block" },
  { key: "album", label: "Album", className: "hidden min-w-0 flex-1 xl:block" },
  { key: "status", label: "Lyrics", className: "w-32 shrink-0" },
  { key: null, label: "Files", className: "hidden w-28 shrink-0 flex-wrap gap-1 lg:flex" },
  { key: null, label: "Made by", className: "hidden w-28 shrink-0 2xl:block" },
  { key: null, label: "Language", className: "hidden w-24 shrink-0 lg:block" },
  { key: "duration", label: "Length", className: "w-12 shrink-0 text-right" },
  { key: "added", label: "Added", className: "hidden w-20 shrink-0 text-right sm:block" },
];

/** An album's cover, from the song's file (or its folder); nothing when it has none. */
function Cover({ id, className }: { id: number; className?: string }) {
  const [gone, setGone] = useState(false);
  return gone ? (
    <span className={cn("flex items-center justify-center rounded bg-muted text-muted-foreground", className)}><Disc3 className="size-1/2" /></span>
  ) : (
    <img src={api.coverUrl(id)} alt="" loading="lazy" onError={() => setGone(true)} className={cn("rounded object-cover", className)} />
  );
}

function Row({ song, compact, open }: { song: SongRow; compact: boolean; open: (id: number) => void }) {
  const busy = song.job === "queued" || song.job === "running";
  return (
    <li>
      <Link to={`/songs/${song.id}`} data-row
            onKeyDown={(e) => e.key === " " && (e.preventDefault(), open(song.id))}
            className="group block text-sm transition-colors hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none">
        {/* A phone: the title in full width, what it has under it. */}
        <div className={cn("flex items-center gap-3 px-4 sm:hidden", compact ? "py-2" : "py-3")}>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{song.title}</p>
            <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              <span className="size-2 shrink-0 rounded-full" style={{ background: STATUS[song.status].color }} aria-label={STATUS[song.status].label} />
              <span className="truncate">{song.artist}{song.album ? ` · ${song.album}` : ""}</span>
            </p>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{length(song.duration)}</span>
        </div>
        {/* Wider: the table's columns. */}
        <div className={cn("hidden items-center gap-3 px-4 sm:flex", compact ? "py-1.5" : "py-2.5")}>
          <div className={COLUMNS[0].className}>
            <p className="truncate font-medium">{song.title}</p>
            {!compact && <p className="truncate text-xs text-muted-foreground md:hidden">{song.artist}</p>}
          </div>
          <span className={cn(COLUMNS[1].className, "truncate text-muted-foreground")}>{song.artist}</span>
          <span className={cn(COLUMNS[2].className, "truncate text-muted-foreground")}>{song.album}</span>
          <div className={cn(COLUMNS[3].className, "flex flex-col items-start gap-0.5")}>
            <StatusBadge status={song.status} />
            {!compact && !!song.to_check && song.to_check > 0 && (
              <span className="text-[11px] text-muted-foreground">{song.to_check} {song.to_check === 1 ? "line" : "lines"} to check</span>
            )}
            {!compact && busy && <span className="text-[11px] text-primary">AI sync {song.job}</span>}
          </div>
          <div className={COLUMNS[4].className}>
            {song.formats.map((format) => (
              <span key={format} className="rounded border border-border px-1.5 text-[10px] font-medium text-muted-foreground uppercase">
                {FORMAT[format] ?? format}
              </span>
            ))}
          </div>
          <span className={cn(COLUMNS[5].className, "truncate text-xs text-muted-foreground")}>{song.made_by.map((m) => SOURCE[m] ?? m).join(", ")}</span>
          <span className={cn(COLUMNS[6].className, "truncate text-xs text-muted-foreground")}>{song.languages.map((l) => l.name).join(", ")}</span>
          <span className={cn(COLUMNS[7].className, "text-xs text-muted-foreground tabular-nums")}>{length(song.duration)}</span>
          <span className={cn(COLUMNS[8].className, "text-xs text-muted-foreground")}>{timeAgo(song.added_at)}</span>
        </div>
      </Link>
    </li>
  );
}

/**
 * The songs as a table: a header that sorts (again: the other way), a row a
 * song, a link each. j / k or the arrows move between rows, Enter opens.
 * grouped: under each album, with its cover.
 */
export function SongTable({ songs, sort, direction, onSort, look }: {
  songs: SongRow[];
  sort: SortKey;
  direction: "asc" | "desc";
  onSort?: (key: SortKey) => void;
  look: Look;
}) {
  const navigate = useNavigate();
  const list = useRef<HTMLDivElement>(null);
  const open = (id: number) => void navigate(`/songs/${id}`);

  const move = (e: KeyboardEvent) => {
    const step = e.key === "j" || e.key === "ArrowDown" ? 1 : e.key === "k" || e.key === "ArrowUp" ? -1 : 0;
    if (!step || e.altKey || e.ctrlKey || e.metaKey) return;
    const rows = [...(list.current?.querySelectorAll<HTMLAnchorElement>("[data-row]") ?? [])];
    const at = rows.indexOf(document.activeElement as HTMLAnchorElement);
    const next = rows[at < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, at + step))];
    if (next) {
      e.preventDefault();
      next.focus();
    }
  };

  const groups: { key: string; album: string; artist: string; first: number; songs: SongRow[] }[] = [];
  if (look.grouped) {
    for (const song of songs) {
      const key = `${song.album}\u0000${song.artist}`;
      const last = groups[groups.length - 1];
      if (last && last.key === key) last.songs.push(song);
      else groups.push({ key, album: song.album || "No album", artist: song.artist, first: song.id, songs: [song] });
    }
  }

  return (
    <div ref={list} onKeyDown={move} className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="hidden items-center gap-3 border-b border-border bg-muted/30 px-4 py-2 text-xs font-medium text-muted-foreground sm:flex" role="row">
        {COLUMNS.map((column) => (
          <div key={column.label} className={cn(column.className, "truncate")} role="columnheader"
               aria-sort={column.key === sort ? (direction === "asc" ? "ascending" : "descending") : undefined}>
            {column.key && onSort ? (
              <button type="button" onClick={() => onSort(column.key!)}
                      className={cn("inline-flex items-center gap-1 hover:text-foreground", column.key === sort && "text-foreground")}>
                {column.label}
                {column.key === sort && (direction === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
              </button>
            ) : column.label}
          </div>
        ))}
      </div>
      {look.grouped ? (
        groups.map((group) => (
          <section key={group.key} className="border-b border-border last:border-0">
            <div className="flex items-center gap-3 bg-muted/20 px-4 py-2">
              <Cover id={group.first} className="size-10 shrink-0" />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{group.album}</p>
                <p className="truncate text-xs text-muted-foreground">{group.artist} · {group.songs.length} {group.songs.length === 1 ? "song" : "songs"}</p>
              </div>
            </div>
            <ul className="divide-y divide-border/60">
              {group.songs.map((song) => <Row key={song.id} song={song} compact={look.compact} open={open} />)}
            </ul>
          </section>
        ))
      ) : (
        <ul className="divide-y divide-border/60">
          {songs.map((song) => <Row key={song.id} song={song} compact={look.compact} open={open} />)}
        </ul>
      )}
    </div>
  );
}

const SIZES = [25, 50, 100, 200];

/** Which page, how many a page: numbers around this one, the first and the last. */
export function Pager({ page, size, total, onPage, onSize }: {
  page: number;
  size: number;
  total: number;
  onPage: (page: number) => void;
  onSize?: (size: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const shown: (number | "…")[] = [];
  for (let n = 1; n <= pages; n++) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 1) shown.push(n);
    else if (shown[shown.length - 1] !== "…") shown.push("…");
  }
  const from = total ? (page - 1) * size + 1 : 0;
  const to = Math.min(total, page * size);
  return (
    <nav className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground" aria-label="Pages">
      <span className="tabular-nums">{from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="Previous page"
                className="rounded-md p-1.5 hover:bg-accent disabled:opacity-30"><ChevronLeft className="size-4" /></button>
        {shown.map((n, i) => n === "…" ? <span key={`gap-${i}`} className="px-1">…</span> : (
          <button key={n} type="button" onClick={() => onPage(n)} aria-current={n === page ? "page" : undefined}
                  className={cn("min-w-7 rounded-md px-1.5 py-1 tabular-nums hover:bg-accent", n === page && "bg-accent font-medium text-foreground")}>
            {n}
          </button>
        ))}
        <button type="button" onClick={() => onPage(page + 1)} disabled={page >= pages} aria-label="Next page"
                className="rounded-md p-1.5 hover:bg-accent disabled:opacity-30"><ChevronRight className="size-4" /></button>
      </div>
      {onSize && (
        <label className="flex items-center gap-2">
          Per page
          <select value={size} onChange={(e) => onSize(Number(e.target.value))}
                  className="rounded-md border border-border bg-background px-2 py-1 text-foreground">
            {SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      )}
    </nav>
  );
}
