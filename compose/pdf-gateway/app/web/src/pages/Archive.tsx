import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Session, type Suggestion } from "../api";
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
  const suggestions = useQuery({ queryKey: ["suggestions", documentId], queryFn: () => api.suggestions(documentId) });
  const scanId = doc.data?.scanId ?? null;
  const paperless = useQuery({
    queryKey: ["paperless", scanId], queryFn: () => api.paperless(scanId!), enabled: !!scanId,
    refetchInterval: (query) => (["PENDING", "IMPORTING", "FAILED_RETRYABLE"].includes(query.state.data?.status ?? "") ? 3000 : false),
  });
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
          {canEdit && scanId && paperless.data && ["NOT_REQUESTED", "FAILED_FINAL"].includes(paperless.data.status) && (
            <Button variant="secondary" data-testid="send-to-paperless" onClick={async () => {
              setError(null);
              try { await api.sendToPaperless(scanId); client.invalidateQueries({ queryKey: ["paperless", scanId] }); }
              catch (failure) { setError(failure); }
            }}>{paperless.data.status === "FAILED_FINAL" ? "Retry sending to Paperless" : "Send to Paperless"}</Button>
          )}
        </div>
        {paperless.data && paperless.data.status !== "NOT_REQUESTED" && (
          <p className="mt-3 text-sm text-slate-600" data-testid="paperless-status">
            Paperless: {PAPERLESS_LABEL[paperless.data.status] ?? paperless.data.status}
            {paperless.data.paperlessDocumentId != null && <> · document #{paperless.data.paperlessDocumentId}</>}
            {paperless.data.lastError && <> · {paperless.data.lastError}</>}
          </p>
        )}
      </Card>
      {suggestions.data?.some((item) => item.status === "PROPOSED") && (
        <Card title="AI suggestions">
          <p className="mb-2 text-xs text-slate-500">Proposed by a local model from this document's text. Nothing changes unless you apply it.</p>
          {suggestions.data.filter((item) => item.status === "PROPOSED").map((item) => (
            <SuggestionCard key={item.id} item={item} canEdit={canEdit} onApply={async () => {
              setError(null);
              try {
                await api.applySuggestion(documentId, item.id, meta.data!.etag);
                setMessage("Suggestion applied; saving…");
                setTimeout(() => { client.invalidateQueries({ queryKey: ["sidecar", documentId] }); client.invalidateQueries({ queryKey: ["suggestions", documentId] }); }, 1500);
              } catch (failure) { setError(failure); }
            }} onDismiss={async () => { await api.dismissSuggestion(documentId, item.id); client.invalidateQueries({ queryKey: ["suggestions", documentId] }); }} />
          ))}
        </Card>
      )}
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

const PAPERLESS_LABEL: Record<string, string> = {
  PENDING: "waiting to be sent", IMPORTING: "sending…", IMPORTED: "copied", FAILED_RETRYABLE: "failed, retrying", FAILED_FINAL: "failed",
};

function SuggestionCard({ item, canEdit, onApply, onDismiss }: { item: Suggestion; canEdit: boolean; onApply: () => void; onDismiss: () => void }) {
  const rows: [string, string, string][] = [];
  for (const key of ["title", "issuer", "documentType", "documentDate"] as const) {
    const field = item.fields[key];
    if (field) rows.push([key, field.value, field.evidence]);
  }
  return (
    <div className="mb-3 rounded-lg bg-slate-50 p-3 text-sm ring-1 ring-slate-200" data-testid="suggestion">
      <ul className="space-y-1">
        {rows.map(([key, value, evidence]) => (
          <li key={key}><span className="font-medium">{key}:</span> {value} <span className="block text-xs text-slate-500">“{evidence}”</span></li>
        ))}
        {item.fields.tags && <li><span className="font-medium">tags:</span> {item.fields.tags.join(", ")}</li>}
      </ul>
      <p className="mt-1 text-xs text-slate-500">{item.model}</p>
      {canEdit && (
        <div className="mt-2 flex gap-2">
          <Button className="min-h-9" onClick={onApply} disabled={!item.fields.title && !item.fields.tags}>Apply title and tags</Button>
          <Button variant="secondary" className="min-h-9" onClick={onDismiss}>Dismiss</Button>
        </div>
      )}
    </div>
  );
}
