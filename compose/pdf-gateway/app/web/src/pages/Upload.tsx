import { useCallback, useEffect, useState, type ChangeEvent, type DragEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, FileText, FolderTree, ScanSearch, ShieldCheck, UploadCloud, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import { api } from "../api";
import { useSession } from "../App";
import { ProgressTrack, useScanProgress } from "../components/progress";
import { Badge, Card, ErrorNote, Muted, PageHeader, Spinner, TERMINAL, cx, formatBytes } from "../components/ui";
import { about } from "../progress";
import { useUploads, type UploadItem } from "../uploads";
import { statusWord } from "../words";

type Report = { finished: boolean; remaining: number | null };

const STAGES: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: ScanSearch, title: "Checked", text: "Viruses and hidden content" },
  { icon: ShieldCheck, title: "Rebuilt clean", text: "Every page redrawn" },
  { icon: FolderTree, title: "Filed", text: "Your archive and Paperless" },
];

/** Add files: drop or choose PDFs; each one starts at once and shows its progress here. */
export function UploadPage() {
  const session = useSession();
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
    <div className="space-y-6">
      <PageHeader title="Add PDF files"
        description="Each file is checked, rebuilt as a clean copy, filed, saved to your archive and added to Paperless. Your original is never released." />
      {session.role === "READ_ONLY" && <ErrorNote error={new Error("This account can only read; it cannot add files.")} />}
      <label onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}
        className={cx("group relative flex min-h-72 cursor-pointer flex-col items-center justify-center gap-4 overflow-hidden rounded-2xl border-2 border-dashed p-8 text-center transition",
          dragging ? "border-primary bg-primary/10" : "border-border bg-card backdrop-blur-xl hover:border-primary/50 hover:bg-primary/5",
          !canUpload && "pointer-events-none opacity-50")}>
        <span className={cx("flex size-16 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-400/20 to-cyan-400/20 text-primary ring-1 ring-primary/30 transition",
          dragging ? "scale-110" : "group-hover:scale-105")}>
          <UploadCloud className="size-8" />
        </span>
        <span>
          <span className="block text-lg font-semibold">{dragging ? "Drop them here" : "Choose PDFs or drop them here"}</span>
          <span className="mt-1 block text-sm text-muted-foreground">Up to 50 MB each · they start right away · you can drop files on any page</span>
        </span>
        <span className="mt-2 hidden items-center gap-3 text-xs text-muted-foreground sm:flex">
          {STAGES.map(({ icon: Icon, title, text }, index) => (
            <span key={title} className="flex items-center gap-3">
              {index > 0 && <ArrowRight className="size-3.5 opacity-50" />}
              <span className="flex items-center gap-2 rounded-full border border-border bg-background/50 py-1 pr-3 pl-1.5">
                <span className="flex size-6 items-center justify-center rounded-full bg-primary/15 text-primary"><Icon className="size-3.5" /></span>
                <span><span className="font-medium text-foreground">{title}</span> · {text}</span>
              </span>
            </span>
          ))}
        </span>
        <input type="file" accept="application/pdf,.pdf" multiple className="sr-only" disabled={!canUpload}
          data-testid="file-input" onChange={(event: ChangeEvent<HTMLInputElement>) => { add(event.target.files); event.target.value = ""; }} />
      </label>
      {items.length > 0 && (
        <Card title="Just added" icon={<FileText className="size-5" />} actions={sent.length > 1 && (
          <span className="text-sm text-muted-foreground" data-testid="batch-summary">
            {done} of {sent.length} done{done < sent.length && left > 0 ? ` · ${about(left)} left` : ""}
          </span>
        )}>
          <ul className="divide-y divide-border" data-testid="upload-list">
            {items.map((item) => <UploadRow key={item.id} item={item} onReport={report} />)}
          </ul>
        </Card>
      )}
      <Muted className="px-1 text-xs">
        Digitally signed PDFs: the signed original is kept as evidence; the clean copy is a picture of its pages and can't carry the signature.
      </Muted>
    </div>
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
  const open = "text-sm font-medium text-primary underline-offset-4 hover:underline";
  return (
    <li className="py-3 text-sm first:pt-0 last:pb-0" data-testid="upload-row">
      <div className="flex flex-wrap items-center gap-3">
        <FileText className="size-5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium" title={item.file.name}>{item.file.name}</span>
          <span className="text-xs text-muted-foreground">{formatBytes(item.file.size)}</span>
        </span>
        {item.error ? <Badge tone="bad">{item.error}</Badge>
          : item.duplicateOf ? <><Badge tone="info">Already in your archive</Badge><Link className={open} to={`/documents/${item.duplicateOf}`} data-testid="duplicate-link">Open</Link></>
          : !item.scanId ? (
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="h-1.5 w-24 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label="Upload progress" aria-valuenow={Math.round(item.progress * 100)}>
                <span className="block h-full rounded-full bg-primary transition-[width]" style={{ width: `${Math.round(item.progress * 100)}%` }} />
              </span>
              sending
            </span>
          ) : <>
              {word && (progress?.finished || word.tone !== "info") && <Badge tone={word.tone}>{word.text}</Badge>}
              {!progress && <Spinner />}
              <Link className={open} to={`/documents/${item.scanId}`}>Open</Link>
            </>}
      </div>
      {progress && !progress.finished && <div className="mt-3 pl-8"><ProgressTrack value={progress} compact /></div>}
    </li>
  );
}
