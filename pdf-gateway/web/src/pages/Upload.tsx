import { useState, type ChangeEvent, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { upload, type Session } from "../api";
import { Badge, Button, Card, ErrorNote, formatBytes } from "../components/ui";

const MAX_BYTES = 50 * 1024 * 1024;

type Item = { id: string; file: File; progress: number; scanId?: string; error?: string };

export function UploadPage({ session }: { session: Session }) {
  const [items, setItems] = useState<Item[]>([]);
  const [intent, setIntent] = useState("ARCHIVE");
  const [dragging, setDragging] = useState(false);
  const canUpload = session.role !== "READ_ONLY";

  const add = (files: FileList | null) => {
    if (!files) return;
    const next = Array.from(files).map((file) => ({
      id: crypto.randomUUID(), file, progress: 0,
      // Checked again by the server; this only saves a pointless upload.
      error: file.size > MAX_BYTES ? "Larger than 50 MB" : !/\.pdf$/i.test(file.name) && file.type !== "application/pdf" ? "Not a PDF" : undefined,
    }));
    setItems((current) => [...next, ...current]);
  };

  const update = (id: string, patch: Partial<Item>) => setItems((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));

  const start = async () => {
    for (const item of items.filter((candidate) => !candidate.scanId && !candidate.error)) {
      try {
        const result = await upload(item.file, intent, (progress) => update(item.id, { progress }));
        update(item.id, { progress: 1, scanId: result.scanId });
      } catch (error) {
        update(item.id, { error: error instanceof Error ? error.message : "Upload refused" });
      }
    }
  };

  const onDrop = (event: DragEvent) => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files); };
  const pending = items.filter((item) => !item.scanId && !item.error).length;

  return (
    <>
      <Card title="Upload PDFs">
        {!canUpload && <ErrorNote error={new Error("This account is read-only and cannot upload.")} />}
        <label onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}
          className={`flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-4 text-center text-sm ${dragging ? "border-slate-900 bg-slate-100" : "border-slate-300"}`}>
          <span className="font-medium">Choose or drop PDF files</span>
          <span className="text-slate-500">Up to 50 MB each. Every file is quarantined and scanned; only a rebuilt copy is released.</span>
          <input type="file" accept="application/pdf,.pdf" multiple className="sr-only" disabled={!canUpload}
            data-testid="file-input" onChange={(event: ChangeEvent<HTMLInputElement>) => { add(event.target.files); event.target.value = ""; }} />
        </label>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <label className="text-sm">
            <span className="mr-2 font-medium">After scanning</span>
            <select value={intent} onChange={(event) => setIntent(event.target.value)} className="min-h-11 rounded-lg border border-slate-300 bg-white px-2">
              <option value="ARCHIVE">Archive</option>
              <option value="SCAN_ONLY">Scan only</option>
            </select>
          </label>
          <Button onClick={start} disabled={!canUpload || pending === 0}>Upload {pending > 0 ? pending : ""}</Button>
        </div>
        <p className="mt-3 text-xs text-slate-500">Signed PDFs: the signed original is kept restricted as evidence; the copy you receive is rebuilt and not signed.</p>
      </Card>
      {items.length > 0 && (
        <Card title="This session">
          <ul className="divide-y divide-slate-100" data-testid="upload-list">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate" title={item.file.name}>{item.file.name}</span>
                <span className="text-slate-500">{formatBytes(item.file.size)}</span>
                {item.error ? <Badge tone="bad">{item.error}</Badge>
                  : item.scanId ? <Link className="font-medium underline" to={`/scans/${item.scanId}`}>View progress</Link>
                  : <progress className="h-2 w-24" max={1} value={item.progress} aria-label="Upload progress" />}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
