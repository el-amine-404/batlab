import { Library, ListFilter, RefreshCw, Rows3, Rows4, Search, SquareLibrary, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Coverage } from "@/components/library/Coverage";
import { type Look, Pager, SongTable } from "@/components/library/SongTable";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type Facet, type LyricsStatus, type SongFilters, type SortKey } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { FORMAT, SOURCE, STATUS, STATUS_ORDER } from "@/lib/format";
import { rememberLibrary, store, stored } from "@/lib/librarySearch";
import { useFacets, useHistory, useSongs, useStatus } from "@/lib/queries";
import { useDebounced } from "@/lib/useDebounced";
import { cn } from "@/lib/utils";

const SORTS: SortKey[] = ["artist", "title", "album", "duration", "added", "status"];
const ANY = "_any";

/** A filter from a list: "Any" and each value, with how many songs have it. */
function Pick({ label, value, options, onChange, name }: {
  label: string;
  value: string;
  options: Facet[];
  onChange: (value?: string) => void;
  name?: (value: string, facet: Facet) => string;
}) {
  return (
    <Select value={value || ANY} onValueChange={(v) => onChange(v === ANY ? undefined : v)}>
      <SelectTrigger className={cn("h-9 w-auto min-w-36", value && "border-primary/50")} aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>{label}: any</SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {name ? name(o.value, o) : o.value} <span className="text-muted-foreground tabular-nums">{o.songs}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The library: its coverage on top (a part clicked narrows to it), then the
 * songs as a table. Everything it is narrowed to, sorted by, and the page,
 * is in the address: back, reload and a shared link keep them.
 */
export function LibraryPage() {
  const can = useCan();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [look, setLook] = useState<Look>(() => stored("stanzarr:library-look", { compact: false, grouped: false }));
  const [size, setSize] = useState<number>(() => stored("stanzarr:library-size", 50));

  const status = (params.get("status") ?? "").split(",").filter((s): s is LyricsStatus => (STATUS_ORDER as string[]).includes(s));
  const typed = params.get("q") ?? "";
  const q = useDebounced(typed.trim(), 250);
  const sort = (SORTS as string[]).includes(params.get("sort") ?? "") ? (params.get("sort") as SortKey) : "artist";
  const direction = params.get("dir") === "desc" ? "desc" : "asc";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const filters: SongFilters = {
    status: status.length ? status : undefined, q, sort, direction,
    language_code: params.get("lang") ?? undefined, format: params.get("format") ?? undefined,
    made_by: params.get("by") ?? undefined, artist: params.get("artist") ?? undefined, album: params.get("album") ?? undefined,
    to_check: params.get("check") === "1" || undefined,
  };
  const { data: counts } = useStatus();
  const { data: facets } = useFacets();
  const { data: history } = useHistory();
  const songs = useSongs(filters, page, size);

  // A song's page leads back here, as it is now.
  useEffect(() => rememberLibrary(location.search), [location.search]);

  const change = (values: Record<string, string | undefined>, replace = false) =>
    setParams((old) => {
      const next = new URLSearchParams(old);
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      if (!("page" in values)) next.delete("page");
      return next;
    }, { replace });

  const toggleStatus = (s: LyricsStatus) =>
    change({ status: (status.includes(s) ? status.filter((x) => x !== s) : [...status, s]).join(",") || undefined });
  const onSort = (key: SortKey) =>
    change({ sort: key === "artist" && direction === "desc" && sort === key ? undefined : key,
             dir: sort === key && direction === "asc" ? "desc" : undefined });
  const setLookStored = (next: Look) => {
    setLook(next);
    store("stanzarr:library-look", next);
  };

  const scan = async () => {
    try {
      await api.scan();
      toast.success("Scanning: Activity shows how far it is.");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't start a scan."));
    }
  };

  const chosen = [
    ...status.map((s) => ({ key: `status-${s}`, label: STATUS[s].label, clear: () => toggleStatus(s) })),
    ...([["lang", "Language"], ["format", "Format"], ["by", "Made by"], ["artist", "Artist"], ["album", "Album"]] as const)
      .filter(([key]) => params.get(key))
      .map(([key, label]) => {
        const value = params.get(key)!;
        const shown = key === "format" ? FORMAT[value] ?? value : key === "by" ? SOURCE[value] ?? value
          : key === "lang" ? facets?.languages.find((l) => l.value === value)?.label ?? value : value;
        return { key, label: `${label}: ${shown}`, clear: () => change({ [key]: undefined }) };
      }),
    ...(filters.to_check ? [{ key: "check", label: "Lines to check", clear: () => change({ check: undefined }) }] : []),
  ];
  const narrowed = chosen.length > 0 || !!q;
  const data = songs.data;

  return (
    <>
      <PageHeader title="Library" description="Every song, and the lyrics it has."
                  actions={can("settings") && <Button variant="secondary" onClick={() => void scan()}><RefreshCw /> Scan now</Button>} />
      {counts && <Coverage counts={counts.by_status} chosen={status} onChoose={toggleStatus} history={history} />}

      <div className="mt-5 mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" value={typed} onChange={(e) => change({ q: e.target.value || undefined }, true)}
                 placeholder="Search titles, artists and albums" className="pl-9" aria-label="Search the library" />
        </div>
        {facets && (
          <>
            <Pick label="Language" value={params.get("lang") ?? ""} options={facets.languages} onChange={(v) => change({ lang: v })}
                  name={(_, f) => f.label ?? f.value} />
            <Pick label="Format" value={params.get("format") ?? ""} options={facets.formats} onChange={(v) => change({ format: v })}
                  name={(v) => FORMAT[v] ?? v} />
            <Pick label="Made by" value={params.get("by") ?? ""} options={facets.made_by} onChange={(v) => change({ by: v })}
                  name={(v) => SOURCE[v] ?? v} />
            <Pick label="Artist" value={params.get("artist") ?? ""} options={facets.artists} onChange={(v) => change({ artist: v })} />
          </>
        )}
        <div className="flex items-center gap-1" role="group" aria-label="How the table looks">
          <button type="button" aria-pressed={look.grouped} title="Group by album, with covers"
                  onClick={() => setLookStored({ ...look, grouped: !look.grouped })}
                  className={cn("rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground", look.grouped && "bg-accent text-foreground")}>
            <SquareLibrary className="size-4" />
          </button>
          <button type="button" aria-pressed={look.compact} title={look.compact ? "Roomy rows" : "Compact rows"}
                  onClick={() => setLookStored({ ...look, compact: !look.compact })}
                  className={cn("rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground", look.compact && "bg-accent text-foreground")}>
            {look.compact ? <Rows4 className="size-4" /> : <Rows3 className="size-4" />}
          </button>
        </div>
      </div>

      {chosen.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
          <ListFilter className="size-3.5 text-muted-foreground" />
          {chosen.map((c) => (
            <button key={c.key} type="button" onClick={c.clear}
                    className="flex items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-1 hover:bg-accent">
              {c.label} <X className="size-3" aria-label="Remove" />
            </button>
          ))}
          <button type="button" onClick={() => setParams(new URLSearchParams())} className="ml-1 text-primary hover:underline">Clear all</button>
        </div>
      )}

      {songs.isPending ? (
        <Loading />
      ) : songs.error ? (
        <Failed error={songs.error} retry={() => void songs.refetch()} />
      ) : !data?.songs.length ? (
        <Empty icon={<Library />} title={narrowed ? "No song like that" : "No song yet"}>
          {narrowed ? "Nothing matches these filters." : "The library's first scan finds them: it runs at start, then every few minutes."}
        </Empty>
      ) : (
        <div className={cn(songs.isPlaceholderData && "opacity-60 transition-opacity")}>
          <SongTable songs={data.songs} sort={sort} direction={direction} onSort={onSort} look={look} />
          <Pager page={page} size={size} total={data.total} onPage={(n) => { change({ page: n > 1 ? String(n) : undefined }); window.scrollTo({ top: 0 }); }}
                 onSize={(n) => { setSize(n); store("stanzarr:library-size", n); change({}); }} />
        </div>
      )}
    </>
  );
}
