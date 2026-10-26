import { useCallback, useEffect, useState, type ChangeEvent, type DragEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Session } from "../api";
import { ProgressTrack, useScanProgress } from "../components/progress";
import { Badge, Card, ErrorNote, Muted, Spinner, TERMINAL, cx, formatBytes } from "../components/ui";
import { about } from "../progress";
import { useUploads, type UploadItem } from "../uploads";
import { statusWord } from "../words";

type Report = { finished: boolean; remaining: number | null };

/** Add files: drop or choose PDFs; each one starts at once and shows its progress here. */
export function UploadPage({ session }: { session: Session }) {
  const { items, add, canUpload } = useUploads();
  const [dragging, setDragging] = useState(false);
  const [reports, setReports] = useState<Record<string, Report>>({});
  const report = useCallback((id: string, value: Report) => setReports((current) =>
    current[id]?.finished === value.finished && current[id]?.remaining === value.remaining ? current : { ...current, [id]: value }), []);

  // Stop the drop from also reaching the drop-anywhere handler (it checks defaultPrevented).
  const onDrop = (event: DragEvent) => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files); };

  const sent = items.filter((item) => item.scanId);
  const done = sent.filter((item) => reports[item.id]?.finished).length;
  const left = Math.max(0, ...sent.filter((item) => !reports[item.id]?.finished).map((item) => reports[item.id]?.remaining ?? 0));

  return (
    <>
      <Card>
        <h1 className="mb-1 text-xl font-semibold">Add PDF files</h1>
        <Muted className="mb-4">
          Each file is checked for viruses and hidden content, rebuilt as a clean copy, filed, saved to your archive and added to Paperless.
          Your original is never released. You can drop files on any page.
        </Muted>
        {session.role === "READ_ONLY" && <ErrorNote error={new Error("This account can only read; it cannot add files.")} />}
        <label onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}
          className={cx("flex min-h-44 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-6 text-center",
            dragging ? "border-slate-900 bg-slate-100 dark:border-slate-200 dark:bg-slate-800" : "border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-900",
            !canUpload && "pointer-events-none opacity-50")}>
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-slate-400">
            <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
          </svg>
          <span className="text-base font-medium">Choose PDFs or drop them here</span>
          <span className="text-sm text-slate-500 dark:text-slate-400">Up to 50 MB each · they start right away</span>
          <input type="file" accept="application/pdf,.pdf" multiple className="sr-only" disabled={!canUpload}
            data-testid="file-input" onChange={(event: ChangeEvent<HTMLInputElement>) => { add(event.target.files); event.target.value = ""; }} />
        </label>
      </Card>
      {items.length > 0 && (
        <Card title="Just added" actions={sent.length > 1 && (
          <span className="text-sm text-slate-600 dark:text-slate-300" data-testid="batch-summary">
            {done} of {sent.length} done{done < sent.length && left > 0 ? ` · ${about(left)} left` : ""}
          </span>
        )}>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="upload-list">
            {items.map((item) => <UploadRow key={item.id} item={item} onReport={report} />)}
          </ul>
        </Card>
      )}
      <Muted className="px-1 text-xs">
        Digitally signed PDFs: the signed original is kept as evidence; the clean copy is a picture of its pages and can't carry the signature.
      </Muted>
    </>
  );
}

function UploadRow({ item, onReport }: { item: UploadItem; onReport: (id: string, value: Report) => void }) {
  const scan = useQuery({
    queryKey: ["scan", item.scanId], queryFn: () => api.scan(item.scanId!), enabled: !!item.scanId,
    refetchInterval: (query) => query.state.data && TERMINAL.has(query.state.data.status) && query.state.data.importStatus !== "PENDING" ? false : 2_000,
  });
  const progress = useScanProgress(item.scanId, scan.data);
  const finished = !!progress?.finished;
  const remaining = progress?.remaining === null || progress?.remaining === undefined ? null : Math.round(progress.remaining / 10) * 10;
  useEffect(() => { if (item.scanId) onReport(item.id, { finished, remaining }); }, [item.id, item.scanId, finished, remaining, onReport]);
  const word = scan.data ? statusWord(scan.data.status) : null;
  return (
    <li className="py-3 text-sm" data-testid="upload-row">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium" title={item.file.name}>{item.file.name}</span>
          <span className="text-xs text-slate-500 dark:text-slate-400">{formatBytes(item.file.size)}</span>
        </span>
        {item.error ? <Badge tone="bad">{item.error}</Badge>
          : item.duplicateOf ? <><Badge tone="info">Already in your archive</Badge><Link className="font-medium underline" to={`/documents/${item.duplicateOf}`} data-testid="duplicate-link">Open</Link></>
          : !item.scanId ? <span className="flex items-center gap-2 text-slate-500"><progress className="h-2 w-24" max={1} value={item.progress} aria-label="Upload progress" /> sending</span>
          : <>
              {word && (progress?.finished || word.tone !== "info") && <Badge tone={word.tone}>{word.text}</Badge>}
              {!progress && <Spinner />}
              <Link className="font-medium underline" to={`/documents/${item.scanId}`}>Open</Link>
            </>}
      </div>
      {progress && !progress.finished && <div className="mt-2"><ProgressTrack value={progress} compact /></div>}
    </li>
  );
}
