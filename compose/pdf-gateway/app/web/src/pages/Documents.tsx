import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Scan } from "../api";
import { Badge, Button, Card, ErrorNote, Muted, Spinner, TERMINAL, cx, formatWhen } from "../components/ui";
import { statusWord } from "../words";

const FILTERS = [
  { id: "all", label: "All", test: () => true },
  { id: "checking", label: "Checking", test: (scan: Scan) => !TERMINAL.has(scan.status) },
  { id: "ready", label: "Ready", test: (scan: Scan) => scan.status === "READY" },
  { id: "held", label: "Held or blocked", test: (scan: Scan) => ["REVIEW_REQUIRED", "BLOCKED", "FAILED"].includes(scan.status) },
] as const;

/** Every upload, newest first: one row per file, whatever became of it. */
export function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  const show = FILTERS.find((filter) => filter.id === params.get("show")) ?? FILTERS[0];
  const [search, setSearch] = useState("");
  const scans = useInfiniteQuery({
    queryKey: ["scans"], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.scans(pageParam), getNextPageParam: (page) => page.nextCursor ?? undefined,
    // Poll only while something is still moving; idle pages make no requests.
    refetchInterval: (query) => query.state.data?.pages.some((page) => page.items.some((scan) => !TERMINAL.has(scan.status))) ? 3_000 : false,
  });
  // Archived documents carry the title and folder the archive gave them.
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api.documents() });
  const archived = new Map((documents.data?.items ?? []).filter((doc) => doc.scanId).map((doc) => [doc.scanId!, doc]));

  const needle = search.trim().toLowerCase();
  const rows = (scans.data?.pages.flatMap((page) => page.items) ?? []).filter((scan) => show.test(scan)).filter((scan) => {
    if (!needle) return true;
    const doc = archived.get(scan.scanId);
    return [scan.displayName, doc?.title, doc?.archivePath].some((text) => text?.toLowerCase().includes(needle));
  });

  return (
    <Card>
      <h1 className="mb-3 text-xl font-semibold">Documents</h1>
      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Show">
        {FILTERS.map((filter) => (
          <button key={filter.id} onClick={() => setParams(filter.id === "all" ? {} : { show: filter.id })}
            className={cx("min-h-9 rounded-full px-3 text-sm ring-1", filter.id === show.id
              ? "bg-slate-900 text-white ring-slate-900 dark:bg-slate-100 dark:text-slate-900 dark:ring-slate-100"
              : "ring-slate-300 hover:bg-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800")}>
            {filter.label}
          </button>
        ))}
      </div>
      <input type="search" placeholder="Search by name, title or folder" value={search} onChange={(event) => setSearch(event.target.value)}
        aria-label="Search" className="mb-2 block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base sm:text-sm dark:border-slate-700 dark:bg-slate-950" />
      <ErrorNote error={scans.error} />
      {scans.data && rows.length === 0 && <Muted className="py-6 text-center">{needle || show.id !== "all" ? "Nothing matches." : "Nothing here yet. Add a PDF to start."}</Muted>}
      <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="document-list">
        {rows.map((scan) => {
          const doc = archived.get(scan.scanId);
          const word = statusWord(scan.status);
          return (
            <li key={scan.scanId}>
              <Link to={`/documents/${scan.scanId}`} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium" title={doc?.title ?? scan.displayName}>{doc?.title ?? scan.displayName}</span>
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    {doc ? <>{folderOf(doc.archivePath)} · {scan.displayName}</> : scan.displayName} · {formatWhen(scan.createdAt)}
                  </span>
                </span>
                <Badge tone={word.tone}>{word.tone === "info" && <Spinner />}<span className={word.tone === "info" ? "ml-1" : ""}>{word.text}</span></Badge>
              </Link>
            </li>
          );
        })}
      </ul>
      {scans.hasNextPage && <Button variant="secondary" className="mt-3 w-full" onClick={() => scans.fetchNextPage()} disabled={scans.isFetchingNextPage}>Show more</Button>}
    </Card>
  );
}

export function folderOf(archivePath: string) {
  return archivePath.split("/").join(" › ");
}
