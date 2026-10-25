import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Session } from "../api";
import { Badge, Button, Card, ErrorNote, Field, formatBytes, formatDate } from "../components/ui";

export function ArchivePage() {
  const [filter, setFilter] = useState("");
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api.documents() });
  const needle = filter.trim().toLowerCase();
  const items = (documents.data?.items ?? []).filter((doc) => !needle || doc.title.toLowerCase().includes(needle) || doc.archivePath.toLowerCase().includes(needle));
  const folders = new Map<string, typeof items>();
  for (const doc of items) {
    const folder = doc.archivePath.split("/").slice(0, -1).join(" / ") || "Archive";
    folders.set(folder, [...(folders.get(folder) ?? []), doc]);
  }

  return (
    <Card title="Archive">
      <Field label="Filter" placeholder="Title or folder" value={filter} onChange={(event) => setFilter(event.target.value)} />
      <ErrorNote error={documents.error} />
      {documents.data && items.length === 0 && <p className="mt-3 text-sm text-slate-500">No documents.</p>}
      <div className="mt-3 space-y-4" data-testid="archive-list">
        {[...folders.entries()].sort().map(([folder, docs]) => (
          <section key={folder}>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{folder}</h3>
            <ul className="divide-y divide-slate-100">
              {docs.map((doc) => (
                <li key={doc.documentId}>
                  <Link to={`/archive/${doc.documentId}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 hover:bg-slate-50">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{doc.title}</span>
                    {doc.reviewState !== "RULE" && doc.reviewState !== "VERIFIED" && <Badge tone="warn">review</Badge>}
                    <span className="text-xs text-slate-500">{formatBytes(doc.bytes)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}

const TAG = /^[a-z0-9][a-z0-9._-]{0,63}$/;

export function DocumentPage({ session }: { session: Session }) {
  const { documentId = "" } = useParams();
  const client = useQueryClient();
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api.document(documentId) });
  const meta = useQuery({ queryKey: ["sidecar", documentId], queryFn: () => api.sidecar(documentId) });
  const [title, setTitle] = useState<string | null>(null);
  const [tags, setTags] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const canEdit = session.role !== "READ_ONLY";

  if (doc.error || meta.error) return <ErrorNote error={doc.error ?? meta.error} />;
  if (!doc.data || !meta.data) return <p className="text-sm text-slate-500">Loading…</p>;
  const sidecar = meta.data.sidecar;
  const titleValue = title ?? sidecar.title;
  const tagsValue = tags ?? (sidecar.tags ?? []).join(", ");

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    const list = tagsValue.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean);
    if (!list.every((tag) => TAG.test(tag))) { setError(new Error("Tags: lowercase letters, digits, dot, dash, underscore.")); return; }
    try {
      const edit = await api.editMetadata(documentId, meta.data.etag, { title: titleValue.trim(), tags: [...new Set(list)] });
      setMessage("Saving…");
      // The archive worker applies the edit; poll its status briefly.
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const status = await api.edit(edit.statusUrl);
        if (status.status !== "PENDING") {
          if (status.status !== "APPLIED") throw new Error(status.status === "CONFLICT" ? "Someone else changed this document; reload and retry." : "The change was refused.");
          setMessage(`Saved (revision ${status.resultRevision}). The PDF itself is unchanged.`);
          setTitle(null); setTags(null);
          await Promise.all([client.invalidateQueries({ queryKey: ["sidecar", documentId] }), client.invalidateQueries({ queryKey: ["document", documentId] }), client.invalidateQueries({ queryKey: ["documents"] })]);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      setMessage("Still saving; check again shortly.");
    } catch (failure) {
      setMessage(null);
      setError(failure instanceof ApiError && failure.code === "REVISION_CONFLICT" ? new Error("Someone else changed this document; reload and retry.") : failure);
    }
  };

  return (
    <>
      <Card title={<span data-testid="document-title">{sidecar.title}</span>} actions={<Link to="/archive" className="text-sm underline">Back</Link>}>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-slate-500">Folder</dt><dd className="break-words">{doc.data.archivePath.split("/").slice(0, -1).join(" / ")}</dd></div>
          <div><dt className="text-xs text-slate-500">Filing</dt><dd>{sidecar.organization.category} · {sidecar.organization.reviewState.toLowerCase()}</dd></div>
          {sidecar.documentDate && <div><dt className="text-xs text-slate-500">Document date</dt><dd>{sidecar.documentDate.value} ({sidecar.documentDate.source})</dd></div>}
          {sidecar.issuer && <div><dt className="text-xs text-slate-500">Issuer</dt><dd>{sidecar.issuer.value} ({sidecar.issuer.reviewState.toLowerCase()})</dd></div>}
          <div><dt className="text-xs text-slate-500">Size</dt><dd>{formatBytes(doc.data.bytes)}</dd></div>
          <div><dt className="text-xs text-slate-500">Published</dt><dd>{formatDate(doc.data.publishedAt)}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-slate-500">SHA-256</dt><dd><code className="break-all text-xs">{doc.data.sha256}</code></dd></div>
        </dl>
        {sidecar.organization.reason && <p className="mt-3 text-xs text-slate-500">Why here: {sidecar.organization.reason}</p>}
        <p className="mt-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 ring-1 ring-slate-200">
          {sidecar.signature.originalSigned ? "The original was digitally signed; this copy is a reconstruction and is not signed." : "This copy is a visual reconstruction of the uploaded file."}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {/* Downloads only: the API serves these as sandboxed attachments, never inline. */}
          <a className="inline-flex min-h-11 items-center rounded-lg bg-slate-900 px-4 text-sm font-medium text-white" href={`/api/v1/archive/${encodeURIComponent(documentId)}/file`} download data-testid="download-pdf">Download PDF</a>
          <a className="inline-flex min-h-11 items-center rounded-lg px-4 text-sm font-medium ring-1 ring-slate-300" href={`/api/v1/archive/${encodeURIComponent(documentId)}/sidecar`} download>Download metadata</a>
        </div>
      </Card>
      <Card title="Edit metadata">
        <form onSubmit={save} className="space-y-3">
          <Field label="Title" value={titleValue} maxLength={200} disabled={!canEdit} onChange={(event) => setTitle(event.target.value)} />
          <Field label="Tags" hint="Comma separated, e.g. finance, paid" value={tagsValue} disabled={!canEdit} onChange={(event) => setTags(event.target.value)} />
          <ErrorNote error={error} />
          {message && <p className="text-sm text-green-800" role="status">{message}</p>}
          <Button type="submit" disabled={!canEdit}>Save</Button>
          <p className="text-xs text-slate-500">Edits change the metadata file only (revision {sidecar.metadataRevision}); the PDF bytes stay as published.</p>
        </form>
      </Card>
    </>
  );
}
