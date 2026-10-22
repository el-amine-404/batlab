import { Library, RefreshCw, Search } from "lucide-react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import { Coverage } from "@/components/library/Coverage";
import { SongRows } from "@/components/library/SongRows";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type LyricsStatus, type SongFilters } from "@/lib/api";
import { useCan } from "@/lib/auth";
import { STATUS, STATUS_ORDER } from "@/lib/format";
import { useSongs, useStatus } from "@/lib/queries";
import { useDebounced } from "@/lib/useDebounced";

const SORTS: { value: NonNullable<SongFilters["sort"]>; label: string }[] = [
  { value: "artist", label: "By artist" },
  { value: "added", label: "Newest first" },
  { value: "title", label: "By title" },
];

/** The library: its coverage on top, then the songs, narrowed in the address (?status=…&q=…), so back and links keep them. */
export function LibraryPage() {
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const status = (STATUS_ORDER as string[]).includes(params.get("status") ?? "") ? (params.get("status") as LyricsStatus) : undefined;
  const typed = params.get("q") ?? "";
  const sort = (params.get("sort") as SongFilters["sort"]) || "artist";
  const q = useDebounced(typed.trim(), 250);
  const filters: SongFilters = { status, q, sort };
  const { data: counts } = useStatus();
  const songs = useSongs(filters);

  const set = (key: string, value?: string) =>
    setParams((old) => {
      const next = new URLSearchParams(old);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: key === "q" });

  const scan = async () => {
    try {
      await api.scan();
      toast.success("Scanning: new songs show here as they're found.");
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't start a scan."));
    }
  };

  const pages = songs.data?.pages ?? [];
  const rows = pages.flatMap((page) => page.songs);
  const total = pages[0]?.total ?? 0;

  return (
    <>
      <PageHeader title="Library" description="Every song, and the lyrics it has."
                  actions={can("settings") && <Button variant="secondary" onClick={() => void scan()}><RefreshCw /> Scan now</Button>} />
      {counts && <Coverage counts={counts.by_status} chosen={status} onChoose={(s) => set("status", s)} />}

      <div className="mt-5 mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" value={typed} onChange={(e) => set("q", e.target.value)} placeholder="Search titles, artists and albums"
                 className="pl-9" aria-label="Search the library" />
        </div>
        <Select value={sort} onValueChange={(value) => set("sort", value === "artist" ? undefined : value)}>
          <SelectTrigger className="w-40" aria-label="Order"><SelectValue /></SelectTrigger>
          <SelectContent>
            {SORTS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {songs.isPending ? (
        <Loading />
      ) : songs.error ? (
        <Failed error={songs.error} retry={() => void songs.refetch()} />
      ) : !rows.length ? (
        <Empty icon={<Library />} title={q || status ? "No song like that" : "No song yet"}>
          {q || status
            ? `Nothing ${status ? `"${STATUS[status].label}"` : ""}${q ? ` matching "${q}"` : ""}.`
            : "The library's first scan finds them: it runs at start, then every few minutes."}
        </Empty>
      ) : (
        <SongRows songs={rows} total={total} more={!!songs.hasNextPage} loadingMore={songs.isFetchingNextPage}
                  onMore={() => void songs.fetchNextPage()} />
      )}
    </>
  );
}
