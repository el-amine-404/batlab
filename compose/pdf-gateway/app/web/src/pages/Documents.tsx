import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { FolderTree, LayoutGrid, List, Search, X } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { api, type Document, type Scan } from "../api";
import { BrowseTree } from "../components/BrowseTree";
import { Preview } from "../components/progress";
import { Badge, Button, ErrorNote, Muted, PageHeader, Segmented, Select, Spinner, TERMINAL, cx, formatWhen, inputClass, primaryLink } from "../components/ui";
import { useLibrary } from "../library";
import { TYPE_LABEL, statusWord } from "../words";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "checking", label: "Checking" },
  { id: "ready", label: "Ready" },
  { id: "held", label: "Held or blocked" },
] as const;

type Row = { key: string; scanId?: string; scan?: Scan; doc?: Document; when: string };
type View = "list" | "grid";

function savedView(): View {
  try { return localStorage.getItem("pgw-view") === "grid" ? "grid" : "list"; } catch { return "list"; }
}

function fold(text: string) {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Everything uploaded and saved: browse by section and correspondent, filter, search the text. */
export function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  const show = FILTERS.find((filter) => filter.id === params.get("show"))?.id ?? "all";
  const section = params.get("section");
  const from = params.get("from");
  const type = params.get("type");
  const year = params.get("year");
  const tag = params.get("tag");
  const [search, setSearch] = useState("");
  const [needle, setNeedle] = useState("");
  const [view, setView] = useState<View>(savedView);
  const [browsing, setBrowsing] = useState(false);
  useEffect(() => { const timer = window.setTimeout(() => setNeedle(search.trim()), 300); return () => window.clearTimeout(timer); }, [search]);

  const scans = useInfiniteQuery({
    queryKey: ["scans"], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.scans(pageParam), getNextPageParam: (page) => page.nextCursor ?? undefined,
    // Poll only while something is still moving; idle pages make no requests.
    refetchInterval: (query) => query.state.data?.pages.some((page) => page.items.some((scan) => !TERMINAL.has(scan.status))) ? 3_000 : false,
  });
  const library = useLibrary();
  // The documents' own text, searched by the server (case, accents and spacing aside).
  const textSearch = useQuery({ queryKey: ["search", needle], queryFn: () => api.search(needle), enabled: needle.length >= 2, staleTime: 30_000 });

  const rows = useMemo(() => {
    const documents = library.data ?? [];
    const byScan = new Map(documents.filter((doc) => doc.scanId).map((doc) => [doc.scanId!, doc]));
    const seen = new Set<string>();
    const out: Row[] = [];
    for (const scan of scans.data?.pages.flatMap((page) => page.items) ?? []) {
      seen.add(scan.scanId);
      out.push({ key: scan.scanId, scanId: scan.scanId, scan, doc: byScan.get(scan.scanId), when: scan.createdAt });
    }
    // Saved documents older than the loaded uploads still belong to the archive.
    for (const doc of documents) {
      if (doc.scanId && seen.has(doc.scanId)) continue;
      out.push({ key: doc.documentId, scanId: doc.scanId ?? undefined, doc, when: doc.publishedAt });
    }
    return out.sort((a, b) => b.when.localeCompare(a.when));
  }, [scans.data, library.data]);

  const hits = new Map((textSearch.data ?? []).map((hit) => [hit.documentId, hit.snippet]));
  const folded = fold(needle);
  const compact = folded.replace(/\s+/g, "");
  const filtered = rows.filter((row) => {
    const status = row.scan?.status ?? "READY";
    if (show === "checking" && TERMINAL.has(status)) return false;
    if (show === "ready" && (status !== "READY" || !row.doc)) return false;
    if (show === "held" && !["REVIEW_REQUIRED", "BLOCKED", "FAILED"].includes(status)) return false;
    if (section || from || type || year || tag) {
      const doc = row.doc;
      if (!doc) return false;
      if (section && doc.category !== section) return false;
      if (from && doc.issuer !== from) return false;
      if (type && doc.documentType !== type) return false;
      if (year && !(doc.documentDate ?? doc.publishedAt).startsWith(year)) return false;
      if (tag && !doc.tags?.includes(tag)) return false;
    }
    if (!folded) return true;
    if (row.doc && hits.has(row.doc.documentId)) return true;
    const text = fold([row.scan?.displayName, row.doc?.title, row.doc?.archivePath, row.doc?.issuer, ...(row.doc?.tags ?? [])].filter(Boolean).join(" "));
    return folded.split(/\s+/).every((word) => text.includes(word)) || (compact.length >= 3 && text.replace(/\s+/g, "").includes(compact));
  });

  // Choices for the filters, from what the archive holds.
  const documents = library.data ?? [];
  const types = [...new Set(documents.map((doc) => doc.documentType).filter((value): value is string => !!value))].sort();
  const years = [...new Set(documents.map((doc) => (doc.documentDate ?? doc.publishedAt).slice(0, 4)))].sort().reverse();
  const tags = [...new Set(documents.flatMap((doc) => doc.tags ?? []))].sort();
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key === "section") next.delete("from");
    setParams(next);
  };
  const narrowed = !!(section || from || type || year || tag || folded || show !== "all");
  const heading = from ? from : section ? section : "Documents";
  const loaded = scans.data && library.data;

  return (
    <div>
      <PageHeader testId="documents-heading"
        title={<>{section && from && <span className="text-muted-foreground">{section} › </span>}{heading}</>}
        description={loaded ? `${filtered.length} of ${rows.length} · checked, rebuilt clean and filed` : undefined}
        actions={<>
          <Button variant="secondary" size="sm" className="lg:hidden" onClick={() => setBrowsing(true)} data-testid="browse"><FolderTree /> Browse</Button>
          <Segmented label="Layout" value={view} onChange={(value) => { setView(value); try { localStorage.setItem("pgw-view", value); } catch { /* storage blocked */ } }}
            options={[{ value: "list", label: <><List /> List</>, testId: "view-list" }, { value: "grid", label: <><LayoutGrid /> Grid</>, testId: "view-grid" }]} />
        </>} />

      <div className="lg:grid lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-8">
        <aside className="hidden lg:block" aria-label="Your archive">
          <div className="sticky top-6">
            <p className="mb-2 px-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Your archive</p>
            <BrowseTree />
          </div>
        </aside>

        <div className="min-w-0 space-y-4">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <input type="search" placeholder="Search titles, names and the documents' text" value={search} onChange={(event) => setSearch(event.target.value)}
              aria-label="Search" data-testid="search" className={cx(inputClass, "h-11 pl-9")} />
            {textSearch.isFetching && <Spinner className="absolute top-3.5 right-3 text-muted-foreground" />}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show">
              {FILTERS.map((filter) => (
                <button key={filter.id} onClick={() => set("show", filter.id === "all" ? null : filter.id)}
                  className={cx("h-8 rounded-full border px-3 text-sm transition", filter.id === show
                    ? "border-primary/50 bg-primary/15 font-medium text-primary" : "border-border text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  {filter.label}
                </button>
              ))}
            </div>
            <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden />
            <Select className="h-8 w-auto rounded-full text-sm" value={type ?? ""} onChange={(event) => set("type", event.target.value || null)} aria-label="Type" data-testid="filter-type">
              <option value="">Any type</option>
              {types.map((value) => <option key={value} value={value}>{TYPE_LABEL[value] ?? value}</option>)}
            </Select>
            <Select className="h-8 w-auto rounded-full text-sm" value={year ?? ""} onChange={(event) => set("year", event.target.value || null)} aria-label="Year" data-testid="filter-year">
              <option value="">Any year</option>
              {years.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            <Select className="h-8 w-auto rounded-full text-sm" value={tag ?? ""} onChange={(event) => set("tag", event.target.value || null)} aria-label="Tag" data-testid="filter-tag">
              <option value="">Any tag</option>
              {tags.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            {narrowed && <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setParams({}); }}><X /> Clear</Button>}
          </div>
          <ErrorNote error={scans.error ?? library.error ?? textSearch.error} />
          {loaded && filtered.length === 0 && (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border py-16 text-center">
              <Muted>{narrowed ? "Nothing matches." : "Nothing here yet."}</Muted>
              {!narrowed && <Link to="/" className={primaryLink}>Add your first PDF</Link>}
            </div>
          )}
          {view === "list" ? (
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card backdrop-blur-xl" data-testid="document-list">
              {filtered.map((row) => <ListRow key={row.key} row={row} snippet={row.doc ? hits.get(row.doc.documentId) : undefined} />)}
            </ul>
          ) : (
            <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4" data-testid="document-grid">
              {filtered.map((row) => <GridCard key={row.key} row={row} />)}
            </ul>
          )}
          {scans.hasNextPage && !narrowed && <Button variant="secondary" className="w-full" onClick={() => scans.fetchNextPage()} disabled={scans.isFetchingNextPage}>Show more</Button>}
        </div>
      </div>

      {browsing && (
        <div className="fixed inset-0 z-50 flex items-end bg-black/60 lg:hidden" onClick={() => setBrowsing(false)} data-testid="browse-sheet">
          <div className="max-h-[80dvh] w-full overflow-y-auto rounded-t-2xl border-t border-border bg-popover p-4 pb-8" onClick={(event) => event.stopPropagation()} role="dialog" aria-label="Browse the archive">
            <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Browse</h2><Button variant="ghost" size="sm" onClick={() => setBrowsing(false)}>Close</Button></div>
            <BrowseTree onPick={() => setBrowsing(false)} />
          </div>
        </div>
      )}
    </div>
  );
}

function rowTitle(row: Row) {
  return row.doc?.title ?? row.scan?.displayName ?? "Document";
}

function StatusBadge({ row }: { row: Row }) {
  const word = statusWord(row.scan?.status ?? "READY");
  return <Badge tone={word.tone}>{word.tone === "info" && <Spinner />}{word.text}</Badge>;
}

function RowLink({ row, className, children }: { row: Row; className: string; children: ReactNode }) {
  return row.scanId ? <Link to={`/documents/${row.scanId}`} className={className}>{children}</Link> : <div className={className}>{children}</div>;
}

function ListRow({ row, snippet }: { row: Row; snippet?: string }) {
  const doc = row.doc;
  return (
    <li>
      <RowLink row={row} className="flex items-center gap-4 px-4 py-3 transition hover:bg-accent/60">
        <Preview documentId={doc?.documentId} version={doc?.currentVersion} size="small" className="h-14 w-10 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium" title={rowTitle(row)}>{rowTitle(row)}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {doc ? <>{folderOf(doc.archivePath)}{doc.documentDate ? ` · ${doc.documentDate}` : ""}</> : row.scan?.displayName} · {formatWhen(row.when)}
          </span>
          {snippet && <span className="mt-0.5 block truncate text-xs text-muted-foreground italic" data-testid="snippet">…{snippet}…</span>}
        </span>
        {doc?.tags && doc.tags.length > 0 && <span className="hidden gap-1 xl:flex">{doc.tags.slice(0, 2).map((value) => <Badge key={value}>{value}</Badge>)}</span>}
        <StatusBadge row={row} />
      </RowLink>
    </li>
  );
}

function GridCard({ row }: { row: Row }) {
  const doc = row.doc;
  const ready = (row.scan?.status ?? "READY") === "READY";
  return (
    <li>
      <RowLink row={row} className="group block overflow-hidden rounded-xl border border-border bg-card backdrop-blur-xl transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-xl hover:shadow-emerald-950/30">
        <div className="relative bg-white/5 p-3">
          <Preview documentId={doc?.documentId} version={doc?.currentVersion} size="small" className="aspect-[3/4] w-full shadow-lg" />
          {!ready && <span className="absolute top-5 right-5"><StatusBadge row={row} /></span>}
        </div>
        <div className="border-t border-border p-3">
          <span className="block truncate text-sm font-medium" title={rowTitle(row)}>{rowTitle(row)}</span>
          <span className="block truncate text-xs text-muted-foreground">{doc?.issuer ?? doc?.category ?? formatWhen(row.when)}</span>
        </div>
      </RowLink>
    </li>
  );
}

export function folderOf(archivePath: string) {
  return archivePath.split("/").join(" › ");
}
