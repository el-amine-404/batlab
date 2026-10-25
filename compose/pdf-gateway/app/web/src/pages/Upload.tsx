import { useState, type ChangeEvent, type DragEvent } from "react";
import { useQueries } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, upload, type Session } from "../api";
import { Badge, Card, ErrorNote, Muted, Spinner, TERMINAL, cx, formatBytes } from "../components/ui";
import { statusWord } from "../words";

const MAX_BYTES = 50 * 1024 * 1024;

type Item = { id: string; file: File; progress: number; scanId?: string; error?: string };

/** Add files: drop or choose PDFs; each one starts at once and shows its progress here. */
export function UploadPage({ session }: { session: Session }) {
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const canUpload = session.role !== "READ_ONLY";
  const update = (id: string, patch: Partial<Item>) => setItems((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));

  const add = async (files: FileList | null) => {
    if (!files || !canUpload) return;
    const next: Item[] = Array.from(files).map((file) => ({
      id: crypto.randomUUID(), file, progress: 0,
      // Checked again by the server; this only saves a pointless upload.
      error: file.size > MAX_BYTES ? "Larger than 50 MB" : !/\.pdf$/i.test(file.name) && file.type !== "application/pdf" ? "Not a PDF" : undefined,
    }));
    setItems((current) => [...next, ...current]);
    for (const item of next.filter((candidate) => !candidate.error)) {
      try {
        // Archive and Paperless: Paperless gets the clean copy once it is ready.
        const result = await upload(item.file, "ARCHIVE_AND_IMPORT", (progress) => update(item.id, { progress }));
        update(item.id, { progress: 1, scanId: result.scanId });
      } catch (error) {
        update(item.id, { error: error instanceof Error ? error.message : "Upload refused" });
      }
    }
  };

  // Live status for this session's uploads, until each one is finished.
  const scans = useQueries({
    queries: items.filter((item) => item.scanId).map((item) => ({
      queryKey: ["scan", item.scanId], queryFn: () => api.scan(item.scanId!),
      refetchInterval: (query: { state: { data?: { status: string } } }) => query.state.data && TERMINAL.has(query.state.data.status) ? false : 2_000,
    })),
  });
  const statusOf = new Map(scans.map((query) => [query.data?.scanId, query.data?.status]));

  const onDrop = (event: DragEvent) => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files); };

  return (
    <>
      <Card>
        <h1 className="mb-1 text-xl font-semibold">Add PDF files</h1>
        <Muted className="mb-4">
          Each file is checked for viruses and hidden content, rebuilt as a clean copy, saved to your archive and added to Paperless.
          Your original is never released.
        </Muted>
        {!canUpload && <ErrorNote error={new Error("This account can only read; it cannot add files.")} />}
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
        <Card title="Just added">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="upload-list">
            {items.map((item) => {
              const status = item.scanId ? statusOf.get(item.scanId) : undefined;
              const word = status ? statusWord(status as never) : null;
              return (
                <li key={item.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium" title={item.file.name}>{item.file.name}</span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">{formatBytes(item.file.size)}</span>
                  </span>
                  {item.error ? <Badge tone="bad">{item.error}</Badge>
                    : !item.scanId ? <span className="flex items-center gap-2 text-slate-500"><progress className="h-2 w-24" max={1} value={item.progress} aria-label="Upload progress" /> sending</span>
                    : <>
                        {word && <Badge tone={word.tone}>{word.tone === "info" && <Spinner />}<span className={word.tone === "info" ? "ml-1" : ""}>{word.text}</span></Badge>}
                        <Link className="font-medium underline" to={`/documents/${item.scanId}`}>Open</Link>
                      </>}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      <Muted className="px-1 text-xs">
        Digitally signed PDFs: the signed original is kept as evidence; the clean copy is a picture of its pages and can't carry the signature.
      </Muted>
    </>
  );
}
