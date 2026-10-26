import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Document, type Scan } from "../api";
import { BrowseTree } from "../components/BrowseTree";
import { Preview } from "../components/progress";
import { Badge, Button, Card, ErrorNote, Muted, Spinner, TERMINAL, cx, formatWhen } from "../components/ui";
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
  const select = "min-h-9 rounded-full border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-950";

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold" data-testid="documents-heading">
          {section && from && <span className="text-slate-500 dark:text-slate-400">{section} › </span>}{heading}
        </h1>
        <div className="flex items-center gap-1">
          <Button variant="secondary" className="min-h-9 lg:hidden" onClick={() => setBrowsing(true)} data-testid="browse">Browse</Button>
          <div className="flex rounded-lg ring-1 ring-slate-300 dark:ring-slate-700" role="group" aria-label="Layout">
            {(["list", "grid"] as const).map((value) => (
              <button key={value} type="button" aria-pressed={view === value} data-testid={`view-${value}`}
                onClick={() => { setView(value); try { localStorage.setItem("pgw-view", value); } catch { /* storage blocked */ } }}
                className={cx("min-h-9 px-3 text-sm first:rounded-l-lg last:rounded-r-lg", view === value ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800")}>
                {value === "list" ? "List" : "Grid"}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="mb-2 flex flex-wrap gap-2" role="group" aria-label="Show">
        {FILTERS.map((filter) => (
          <button key={filter.id} onClick={() => set("show", filter.id === "all" ? null : filter.id)}
            className={cx("min-h-9 rounded-full px-3 text-sm ring-1", filter.id === show
              ? "bg-slate-900 text-white ring-slate-900 dark:bg-slate-100 dark:text-slate-900 dark:ring-slate-100"
              : "ring-slate-300 hover:bg-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800")}>
            {filter.label}
          </button>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <select className={select} value={type ?? ""} onChange={(event) => set("type", event.target.value || null)} aria-label="Type" data-testid="filter-type">
          <option value="">Any type</option>
          {types.map((value) => <option key={value} value={value}>{TYPE_LABEL[value] ?? value}</option>)}
        </select>
        <select className={select} value={year ?? ""} onChange={(event) => set("year", event.target.value || null)} aria-label="Year" data-testid="filter-year">
          <option value="">Any year</option>
          {years.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select className={select} value={tag ?? ""} onChange={(event) => set("tag", event.target.value || null)} aria-label="Tag" data-testid="filter-tag">
          <option value="">Any tag</option>
          {tags.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        {narrowed && <Button variant="ghost" className="min-h-9" onClick={() => { setSearch(""); setParams({}); }}>Clear</Button>}
      </div>
      <div className="relative mb-2">
        <input type="search" placeholder="Search titles, names and the documents' text" value={search} onChange={(event) => setSearch(event.target.value)}
          aria-label="Search" data-testid="search" className="block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base sm:text-sm dark:border-slate-700 dark:bg-slate-950" />
        {textSearch.isFetching && <span className="absolute right-3 top-3 text-slate-400"><Spinner /></span>}
      </div>
      <ErrorNote error={scans.error ?? library.error ?? textSearch.error} />
      {scans.data && library.data && filtered.length === 0 && (
        <Muted className="py-6 text-center">{narrowed ? "Nothing matches." : "Nothing here yet. Add a PDF to start."}</Muted>
      )}
      {view === "list" ? (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="document-list">
          {filtered.map((row) => <ListRow key={row.key} row={row} snippet={row.doc ? hits.get(row.doc.documentId) : undefined} />)}
        </ul>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="document-grid">
          {filtered.map((row) => <GridCard key={row.key} row={row} />)}
        </ul>
      )}
      {scans.hasNextPage && !narrowed && <Button variant="secondary" className="mt-3 w-full" onClick={() => scans.fetchNextPage()} disabled={scans.isFetchingNextPage}>Show more</Button>}
      {browsing && (
        <div className="fixed inset-0 z-40 flex items-end bg-slate-900/40 lg:hidden" onClick={() => setBrowsing(false)} data-testid="browse-sheet">
          <div className="max-h-[80dvh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 pb-8 dark:bg-slate-900" onClick={(event) => event.stopPropagation()} role="dialog" aria-label="Browse the archive">
            <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Browse</h2><Button variant="ghost" onClick={() => setBrowsing(false)}>Close</Button></div>
            <BrowseTree onPick={() => setBrowsing(false)} />
          </div>
        </div>
      )}
    </Card>
  );
}

function rowTitle(row: Row) {
  return row.doc?.title ?? row.scan?.displayName ?? "Document";
}

function StatusBadge({ row }: { row: Row }) {
  const word = statusWord(row.scan?.status ?? "READY");
  return <Badge tone={word.tone}>{word.tone === "info" && <Spinner />}<span className={word.tone === "info" ? "ml-1" : ""}>{word.text}</span></Badge>;
}

function RowLink({ row, className, children }: { row: Row; className: string; children: ReactNode }) {
  return row.scanId ? <Link to={`/documents/${row.scanId}`} className={className}>{children}</Link> : <div className={className}>{children}</div>;
}

function ListRow({ row, snippet }: { row: Row; snippet?: string }) {
  const doc = row.doc;
  return (
    <li>
      <RowLink row={row} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/60">
        <Preview documentId={doc?.documentId} version={doc?.currentVersion} size="small" className="h-14 w-10 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium" title={rowTitle(row)}>{rowTitle(row)}</span>
          <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
            {doc ? <>{folderOf(doc.archivePath)}{doc.documentDate ? ` · ${doc.documentDate}` : ""}</> : row.scan?.displayName} · {formatWhen(row.when)}
          </span>
          {snippet && <span className="mt-0.5 block truncate text-xs italic text-slate-500 dark:text-slate-400" data-testid="snippet">…{snippet}…</span>}
        </span>
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
      <RowLink row={row} className="block rounded-xl p-2 ring-1 ring-slate-200 hover:bg-slate-50 dark:ring-slate-800 dark:hover:bg-slate-800/60">
        <Preview documentId={doc?.documentId} version={doc?.currentVersion} size="small" className="aspect-[3/4] w-full" />
        <span className="mt-2 block truncate text-sm font-medium" title={rowTitle(row)}>{rowTitle(row)}</span>
        <span className="flex items-center justify-between gap-1 text-xs text-slate-500 dark:text-slate-400">
          <span className="truncate">{doc?.issuer ?? doc?.category ?? formatWhen(row.when)}</span>
          {!ready && <StatusBadge row={row} />}
        </span>
      </RowLink>
    </li>
  );
}

export function folderOf(archivePath: string) {
  return archivePath.split("/").join(" › ");
}
