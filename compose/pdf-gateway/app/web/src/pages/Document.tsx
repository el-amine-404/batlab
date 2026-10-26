import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, Scan, type EnrichmentJob, type Findings, type PaperlessImport, type Session, type Suggestion } from "../api";
import {
  Badge, Button, Card, Details, ErrorNote, Field, Muted, Notice, Steps, TERMINAL, formatBytes, formatDate, formatWhen,
  primaryLink, secondaryLink, type StepState,
} from "../components/ui";
import { AI_STATE, FIELD_LABEL, safetySteps, verdict } from "../words";
import { folderOf } from "./Documents";

const TAG = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const MOVING = new Set(["WAITING_FOR_AI", "LEASED", "SUBMITTED"]);

/** One page per uploaded file: what happened to it, and the clean copy it became. */
export function DocumentPage({ session }: { session: Session }) {
  const { scanId = "" } = useParams();
  const client = useQueryClient();
  const canEdit = session.role !== "READ_ONLY";

  const scan = useQuery({
    queryKey: ["scan", scanId], queryFn: () => api.scan(scanId),
    // Fallback when server-sent events are unavailable (e.g. a proxy buffers them).
    refetchInterval: (query) => query.state.data && TERMINAL.has(query.state.data.status) ? false : 4_000,
  });
  const status = scan.data?.status;
  const findings = useQuery({ queryKey: ["findings", scanId, status], queryFn: () => api.findings(scanId), enabled: !!scan.data });
  const documentId = findings.data?.archive?.documentId ?? null;
  const ready = status === "READY" && !!documentId;
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api.document(documentId!), enabled: ready });
  const meta = useQuery({ queryKey: ["sidecar", documentId], queryFn: () => api.sidecar(documentId!), enabled: ready });
  const paperless = useQuery({
    queryKey: ["paperless", scanId], queryFn: () => api.paperless(scanId), enabled: !!scan.data,
    refetchInterval: (query) => ["PENDING", "IMPORTING", "FAILED_RETRYABLE"].includes(query.state.data?.status ?? "") ? 3_000 : false,
  });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, staleTime: 30_000 });
  const jobs = useQuery({
    queryKey: ["enrichment", scanId], queryFn: () => api.scanEnrichment(scanId), enabled: !!scan.data,
    refetchInterval: (query) => query.state.data?.some((job) => MOVING.has(job.status)) || !TERMINAL.has(status ?? "") ? 5_000 : false,
  });
  const suggestions = useQuery({ queryKey: ["suggestions", documentId, jobs.data?.map((job) => job.status).join()], queryFn: () => api.suggestions(documentId!), enabled: ready });

  useEffect(() => {
    if (!scan.data || TERMINAL.has(scan.data.status)) return;
    const source = new EventSource(`/api/v1/scans/${encodeURIComponent(scanId)}/events`);
    source.addEventListener("status", (event) => {
      const parsed = Scan.safeParse(JSON.parse((event as MessageEvent).data));
      if (parsed.success) client.setQueryData(["scan", scanId], parsed.data);
    });
    source.onerror = () => source.close();
    return () => source.close();
  }, [scanId, scan.data?.status, client]);

  if (scan.error) return <ErrorNote error={scan.error instanceof ApiError && scan.error.status === 404 ? new Error("This document doesn't exist or isn't yours.") : scan.error} />;
  if (!scan.data) return <Muted>Loading…</Muted>;
  const view = scan.data;
  const sidecar = meta.data?.sidecar;
  const v = verdict(view);
  const reload = () => Promise.all(["sidecar", "document", "documents", "suggestions", "enrichment", "paperless"].map((key) => client.invalidateQueries({ queryKey: [key] })));

  const safety = safetySteps(view, findings.data).map((step) => step.key === "archive" && ready && doc.data
    ? { ...step, detail: `In ${folderOf(doc.data.archivePath)}.` } : step);
  const steps = [
    ...safety.slice(0, -1),
    // The AI files the document before it is saved, so its step comes first.
    ...aiStep(view, ai.data?.state, ai.data?.enabled, sidecar?.organization.reviewState, jobs.data, suggestions.data),
    safety[safety.length - 1],
    paperlessStep(view, paperless.data, canEdit, async () => { await api.sendToPaperless(scanId); await client.invalidateQueries({ queryKey: ["paperless", scanId] }); }),
  ];

  return (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="break-words text-xl font-semibold" data-testid="document-title">{sidecar?.title ?? view.displayName}</h1>
          <Muted className="break-all">{view.displayName} · {formatBytes(view.sourceBytes)} · added {formatWhen(view.createdAt)}</Muted>
        </div>
        <Link to="/documents" className="shrink-0 text-sm underline">All documents</Link>
      </div>

      <Notice tone={v.tone} title={v.title}>{v.text}</Notice>

      {ready && doc.data && sidecar && meta.data && (
        <CopyCard documentId={documentId!} doc={doc.data} sidecar={sidecar} etag={meta.data.etag} paperless={paperless.data}
          canEdit={canEdit} onSaved={reload} aiEnabled={ai.data?.enabled === true}
          aiBusy={!!jobs.data?.some((job) => job.kind === "SUGGEST" && MOVING.has(job.status))}
          onRefile={async () => { await api.refile(documentId!); await client.invalidateQueries({ queryKey: ["enrichment", scanId] }); }} />
      )}

      {ready && meta.data && suggestions.data?.some((item) => item.status === "PROPOSED") && (
        <Card title="Suggestions from the AI">
          <Muted className="mb-3">
            Read by a model on your laptop from this document's text. Each one shows the words it came from. Nothing changes unless you use it.
          </Muted>
          {suggestions.data.filter((item) => item.status === "PROPOSED").map((item) => (
            <SuggestionCard key={item.id} item={item} canEdit={canEdit}
              onApply={async () => {
                await api.applySuggestion(documentId!, item.id, meta.data!.etag);
                setTimeout(reload, 1500);
              }}
              onDismiss={async () => { await api.dismissSuggestion(documentId!, item.id); await reload(); }} />
          ))}
        </Card>
      )}

      <Card title="What happened">
        <Steps items={steps} />
      </Card>

      {TERMINAL.has(view.status) && canEdit && <DeleteCard scan={view} ready={ready} />}

      <TechnicalDetails scan={view} findings={findings.data} sidecar={sidecar} documentId={documentId} doc={doc.data} />
    </>
  );
}

function CopyCard({ documentId, doc, sidecar, etag, paperless, canEdit, onSaved, aiEnabled, aiBusy, onRefile }: {
  documentId: string; doc: NonNullable<Awaited<ReturnType<typeof api.document>>>; sidecar: Awaited<ReturnType<typeof api.sidecar>>["sidecar"];
  etag: string; paperless?: PaperlessImport; canEdit: boolean; onSaved: () => Promise<unknown>;
  aiEnabled: boolean; aiBusy: boolean; onRefile: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const rows: [string, ReactNode][] = [
    ["Title", sidecar.title],
    ["From", sidecar.issuer?.value],
    ["Type", sidecar.documentType ? TYPE_LABEL[sidecar.documentType] ?? sidecar.documentType : undefined],
    ["Date", sidecar.documentDate?.value],
    ["Number", sidecar.reference?.value],
    ["Amount", sidecar.amount ? `${sidecar.amount.value} ${sidecar.amount.currency}` : undefined],
    ["Folder", folderOf(doc.archivePath)],
    ["Tags", sidecar.tags?.length ? sidecar.tags.map((tag) => <Badge key={tag}>{tag}</Badge>) : undefined],
  ];
  const state = sidecar.organization.reviewState;
  const byAi = state === "AI";
  const embedded = sidecar.embeddedMetadata?.values ?? {};
  // A saved change reaches the PDF through a new version, checked again first: show it until then.
  const updating = Object.keys(embedded).length > 0 && embedded.Title !== plain(sidecar.title);
  const file = `/api/v1/archive/${encodeURIComponent(documentId)}/file`;
  return (
    <Card title="Your clean copy" actions={canEdit && !editing && <Button variant="ghost" onClick={() => setEditing(true)} data-testid="edit">Edit</Button>}>
      {!editing && (
        <p className="mb-3 text-xs" data-testid="filed-by">
          {byAi ? <Badge tone="info">Filled in by the AI</Badge> : state === "RULE" ? <Badge tone="good">Filed by your rules</Badge>
            : state === "VERIFIED" ? <Badge tone="good">Filed by you</Badge> : <Badge tone="warn">Not filed yet</Badge>}
          <span className="ml-2 text-slate-500 dark:text-slate-400">
            {byAi ? "Every value was checked against the document's text; correct anything that's off with Edit."
              : state === "RULE" ? "A filing rule recognised it." : state === "VERIFIED" ? "" : "Give it a title and folder with Edit, or re-run the AI."}
          </span>
        </p>
      )}
      {editing ? <EditForm documentId={documentId} sidecar={sidecar} etag={etag} onDone={async (saved) => { setEditing(false); if (saved) await onSaved(); }} /> : (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2 text-sm" data-testid="copy-fields">
          {rows.filter(([, value]) => value !== undefined && value !== "").map(([label, value]) => (
            <div key={label} className="contents"><dt className="text-slate-500 dark:text-slate-400">{label}</dt><dd className="flex min-w-0 flex-wrap gap-1 break-words">{value}</dd></div>
          ))}
        </dl>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {/* View opens the browser's own PDF viewer on the clean copy (validated: no scripts, links or forms). */}
        <a className={primaryLink} href={`${file}?view=true`} target="_blank" rel="noopener noreferrer" data-testid="view-pdf">View</a>
        <a className={secondaryLink} href={file} download data-testid="download-pdf">Download</a>
        {paperless?.paperlessUrl && (
          <a className={secondaryLink} href={paperless.paperlessUrl} target="_blank" rel="noopener noreferrer" data-testid="open-paperless">Open in Paperless</a>
        )}
        {canEdit && aiEnabled && (
          <Button variant="secondary" disabled={aiBusy} data-testid="refile" onClick={async () => {
            setError(null);
            try { await onRefile(); } catch (failure) { setError(failure); }
          }}>{aiBusy ? "The AI is on it…" : "Re-run the AI"}</Button>
        )}
      </div>
      <ErrorNote error={error} />
      {Object.keys(embedded).length > 0 && (
        <details className="mt-4 text-sm" data-testid="inside-pdf">
          <summary className="cursor-pointer text-slate-600 dark:text-slate-300">Inside the PDF {updating && <Badge tone="info">updating…</Badge>}</summary>
          <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1">
            {["Title", "Author", "Subject", "Keywords"].filter((key) => embedded[key]).map((key) => (
              <div key={key} className="contents"><dt className="text-slate-500 dark:text-slate-400">{key}</dt><dd className="break-words">{embedded[key]}</dd></div>
            ))}
          </dl>
          <Muted className="mt-1 text-xs">No producer, creator or dates are written. Fast web view is on. Version {sidecar.artifactVersion ?? 1}.</Muted>
        </details>
      )}
      <Muted className="mt-3 text-xs">
        The clean copy looks like your original: each page was redrawn as a lossless image, with an invisible text layer
        so you can search and copy text. {formatBytes(doc.bytes)}.
      </Muted>
    </Card>
  );
}

/** The same plain punctuation the archive writes into the PDF. */
function plain(text: string) {
  return text.replace(/[\u2013\u2014\u2012\u2212\u00b7\u2022]/g, "-").replace(/[\u201c\u201d\u201e\u00ab\u00bb\u2018\u2019]/g, "'")
    .replace(/\u2026/g, "...").replace(/\s+/g, " ").trim();
}

const SECTIONS = ["Finance", "Employment", "Identity", "Medical", "Education", "Contracts", "Property", "Vehicles", "Taxes", "Personal", "Other", "Unsorted"];
const select = "block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base sm:text-sm dark:border-slate-700 dark:bg-slate-950";

function EditForm({ documentId, sidecar, etag, onDone }: {
  documentId: string; sidecar: Awaited<ReturnType<typeof api.sidecar>>["sidecar"]; etag: string; onDone: (saved: boolean) => void;
}) {
  const [title, setTitle] = useState(sidecar.title);
  const [issuer, setIssuer] = useState(sidecar.issuer?.value ?? "");
  const [kind, setKind] = useState(sidecar.documentType ?? "");
  const [date, setDate] = useState(sidecar.documentDate?.value ?? "");
  const [reference, setReference] = useState(sidecar.reference?.value ?? "");
  const [amount, setAmount] = useState(sidecar.amount?.value ?? "");
  const [currency, setCurrency] = useState(sidecar.amount?.currency ?? "");
  const [section, setSection] = useState(sidecar.organization.category);
  const [tags, setTags] = useState((sidecar.tags ?? []).join(", "));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const list = tags.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean);
    if (!list.every((tag) => TAG.test(tag))) { setError(new Error("Tags: lowercase letters, digits, dot, dash or underscore, separated by commas.")); return; }
    if ((amount && !/^-?\d{1,12}(\.\d{1,4})?$/.test(amount)) || (amount && !/^[A-Z]{3}$/.test(currency.toUpperCase()))) {
      setError(new Error("Amount: a number like 1015.68 and a three-letter currency like MAD.")); return;
    }
    if (section !== "Unsorted" && (!kind || !date)) {
      setError(new Error("To file it under a section, give it a type and a date (or choose Unsorted).")); return;
    }
    setBusy(true);
    try {
      const edit = await api.editMetadata(documentId, etag, {
        title: title.trim(), tags: [...new Set(list)], issuer: issuer.trim() || null, documentType: kind || null,
        documentDate: date || null, reference: reference.trim() || null,
        amount: amount ? { value: amount, currency: currency.toUpperCase() } : null, section,
      });
      // The archive worker applies the edit; poll its status briefly.
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const status = await api.edit(edit.statusUrl);
        if (status.status === "APPLIED") { onDone(true); return; }
        if (status.status !== "PENDING") throw new Error(status.status === "CONFLICT" ? "It was changed meanwhile; reload the page and try again." : "The change was refused.");
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      throw new Error("Still saving; check again in a moment.");
    } catch (failure) {
      setError(failure instanceof ApiError && failure.code === "REVISION_CONFLICT" ? new Error("It was changed meanwhile; reload the page and try again.") : failure);
    } finally { setBusy(false); }
  };
  return (
    <form onSubmit={save} className="space-y-3" data-testid="edit-form">
      <Field label="Title" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
      <Field label="From (correspondent)" value={issuer} maxLength={120} onChange={(event) => setIssuer(event.target.value)} />
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm"><span className="mb-1 block font-medium">Type</span>
          <select className={select} value={kind} onChange={(event) => setKind(event.target.value)} name="documentType">
            <option value="">-</option>
            {Object.entries(TYPE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <Field label="Date" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        <Field label="Number" value={reference} maxLength={60} onChange={(event) => setReference(event.target.value)} />
        <div className="grid grid-cols-[1fr_5rem] gap-2">
          <Field label="Amount" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="1015.68" />
          <Field label="Currency" value={currency} maxLength={3} onChange={(event) => setCurrency(event.target.value)} placeholder="MAD" />
        </div>
        <label className="block text-sm"><span className="mb-1 block font-medium">Section</span>
          <select className={select} value={section} onChange={(event) => setSection(event.target.value)} name="section">
            {SECTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>
      <Field label="Tags" hint="Separated by commas, e.g. finance, paid" value={tags} onChange={(event) => setTags(event.target.value)} />
      <ErrorNote error={error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        <Button type="button" variant="secondary" onClick={() => onDone(false)}>Cancel</Button>
      </div>
      <Muted className="text-xs">
        Saving files it again (folder and file name follow), updates Paperless, and writes a new version of the PDF
        with these values; the new version is checked again before it replaces the current one.
      </Muted>
    </form>
  );
}

function SuggestionCard({ item, canEdit, onApply, onDismiss }: { item: Suggestion; canEdit: boolean; onApply: () => Promise<void>; onDismiss: () => Promise<void> }) {
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const run = (action: () => Promise<void>) => async () => {
    setBusy(true); setError(null);
    try { await action(); } catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  const rows = (["title", "issuer", "documentType", "documentDate"] as const).flatMap((key) => item.fields[key] ? [[key, item.fields[key]!] as const] : []);
  return (
    <div className="mb-3 rounded-lg bg-slate-50 p-3 text-sm ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800" data-testid="suggestion">
      <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2">
        {rows.map(([key, field]) => (
          <div key={key} className="contents">
            <dt className="text-slate-500 dark:text-slate-400">{FIELD_LABEL[key]}</dt>
            <dd className="min-w-0 break-words">{field.value}<span className="block text-xs text-slate-500 dark:text-slate-400">from “{field.evidence}”</span></dd>
          </div>
        ))}
        {item.fields.tags && item.fields.tags.length > 0 && (
          <div className="contents"><dt className="text-slate-500 dark:text-slate-400">Tags</dt><dd className="flex flex-wrap gap-1">{item.fields.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}</dd></div>
        )}
      </dl>
      <ErrorNote error={error} />
      {canEdit && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button className="min-h-9" disabled={busy || (!item.fields.title && !item.fields.tags)} onClick={run(onApply)}>Use title and tags</Button>
          <Button variant="secondary" className="min-h-9" disabled={busy} onClick={run(onDismiss)}>Not now</Button>
        </div>
      )}
    </div>
  );
}

type Step = { key: string; label: ReactNode; detail?: ReactNode; state: StepState };

function paperlessStep(scan: Scan, item: PaperlessImport | undefined, canEdit: boolean, send: () => Promise<void>): Step {
  const label = "Added to Paperless";
  if (["REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"].includes(scan.status)) return { key: "paperless", label, state: "skipped", detail: "Not sent: the file wasn't released." };
  if (!item) return { key: "paperless", label, state: "waiting" };
  // Asked for at upload: the request itself is recorded when the clean copy is archived.
  if (item.status === "NOT_REQUESTED" && scan.importStatus === "PENDING")
    return { key: "paperless", label, state: scan.status === "READY" ? "running" : "waiting", detail: <span data-testid="paperless-status">{scan.status === "READY" ? "Sending the clean copy…" : "After the checks."}</span> };
  const button = (text: string) => canEdit && <Button variant="secondary" className="mt-2 min-h-9" data-testid="send-to-paperless" onClick={() => { send().catch(() => undefined); }}>{text}</Button>;
  switch (item.status) {
    case "IMPORTED":
      return { key: "paperless", label, state: "done", detail: <span data-testid="paperless-status">Added as document #{item.paperlessDocumentId}{item.paperlessUrl && <> · <a className="underline" href={item.paperlessUrl} target="_blank" rel="noopener noreferrer">open it</a></>}.</span> };
    case "PENDING": case "IMPORTING":
      return { key: "paperless", label, state: scan.status === "READY" ? "running" : "waiting", detail: <span data-testid="paperless-status">{scan.status === "READY" ? "Sending the clean copy…" : "After the checks."}</span> };
    case "FAILED_RETRYABLE":
      return { key: "paperless", label, state: "running", detail: <span data-testid="paperless-status">Paperless didn't answer; trying again{item.lastError ? ` (${item.lastError})` : ""}.</span> };
    case "FAILED_FINAL":
      return { key: "paperless", label, state: "failed", detail: <><span data-testid="paperless-status">Paperless refused it{item.lastError ? ` (${item.lastError})` : ""}.</span>{button("Try again")}</> };
    default:
      return { key: "paperless", label: "Paperless", state: "skipped", detail: <>Not added to Paperless.{scan.status === "READY" && button("Add to Paperless")}</> };
  }
}

function aiStep(scan: Scan, state: string | undefined, enabled: boolean | undefined, reviewState: string | undefined,
                jobs: EnrichmentJob[] | undefined, suggestions: Suggestion[] | undefined): Step[] {
  if (enabled === false) return [];
  const label = "Filed by the AI (on your laptop)";
  const job = jobs?.find((item) => item.kind === "SUGGEST");
  const away = state && !["READY", "AVAILABLE", "BUSY"].includes(state) ? ` ${AI_STATE[state] ?? state}.` : "";
  if (["REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"].includes(scan.status)) return [{ key: "ai", label, state: "skipped" }];
  if (!job) {
    if (scan.status === "READY") {
      return [{ key: "ai", label, state: "skipped", detail: reviewState === "RULE" ? "Not needed: your filing rules recognised it." : "Not asked for this document." }];
    }
    return [{ key: "ai", label, state: "waiting", detail: "If your filing rules don't recognise it." }];
  }
  const applied = job.applied ? Object.keys(job.applied).length : 0;
  const proposed = suggestions?.filter((item) => item.status === "PROPOSED").length ?? 0;
  switch (job.status) {
    case "WAITING_FOR_AI":
      return [{ key: "ai", label, state: scan.status === "READY" ? "waiting" : "running",
        detail: scan.status === "READY" ? `Saved without it; the AI files it when the laptop is back.${away}`
          : `Waiting for the AI laptop (up to 10 min, then it's saved as it is).${away}` }];
    case "LEASED": case "SUBMITTED":
      return [{ key: "ai", label, state: "running", detail: "The AI is reading it (about a minute)…" }];
    case "COMPLETED":
      return [{ key: "ai", label, state: "done", detail: proposed ? "You had filed it already; its answer is a suggestion (above)."
        : applied ? "Title, correspondent, type, date and folder filled in from the document's text." : "Nothing in the text was certain enough to use." }];
    case "FAILED": return [{ key: "ai", label, state: "failed", detail: `The AI's answer couldn't be used${job.errorCode ? ` (${job.errorCode})` : ""}; saved without it.` }];
    default: return [{ key: "ai", label, state: "skipped", detail: "Gave up waiting for the laptop." }];
  }
}

const TYPE_LABEL: Record<string, string> = {
  invoice: "Invoice", receipt: "Receipt", bank_statement: "Bank statement", payslip: "Payslip", contract: "Contract",
  tax_document: "Tax document", insurance: "Insurance", medical: "Medical", identity_document: "Identity document",
  certificate: "Certificate", letter: "Letter", school_document: "School document", presentation: "Presentation",
  report: "Report", manual: "Manual", other: "Document",
};

function DeleteCard({ scan, ready }: { scan: Scan; ready: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  const signed = scan.originalRetention === "SIGNED_RESTRICTED";
  const remove = async () => {
    const what = ready ? "the clean copy in your archive and in Paperless (Paperless keeps it in its trash for a while), the original" : "the held original";
    if (!window.confirm(`Delete ${what} and every record of it? Your own file on your computer is not touched.${signed ? " The original is digitally signed and kept as evidence." : ""}`)) return;
    try {
      await api.deleteEverywhere(scan.scanId, signed);
      await client.invalidateQueries({ queryKey: ["scans"] });
      await client.invalidateQueries({ queryKey: ["documents"] });
      navigate("/documents");
    } catch (failure) { setError(failure); }
  };
  return (
    <Details summary="Delete">
      <Muted className="mb-3">
        Removes it everywhere: the archived copy, the original kept in quarantine, the reports, and the Paperless copy.
      </Muted>
      <ErrorNote error={error} />
      <Button variant="danger" onClick={remove} data-testid="delete">Delete everywhere</Button>
    </Details>
  );
}

function TechnicalDetails({ scan, findings, sidecar, documentId, doc }: {
  scan: Scan; findings?: Findings; sidecar?: { metadataRevision: number; organization: { reason?: string } };
  documentId: string | null; doc?: { sha256: string; publishedAt: string };
}) {
  const f = findings;
  return (
    <Details summary="Technical details">
      <div className="space-y-4 text-sm">
        {f?.analysis && (
          <section>
            <h3 className="mb-1 font-medium">Scan findings</h3>
            {f.analysis.findings.length === 0 ? <Muted>No indicators reported.</Muted> : (
              <ul className="space-y-1" data-testid="findings">
                {f.analysis.findings.map((item, index) => (
                  <li key={index} className="flex flex-wrap items-center gap-2">
                    <Badge tone={item.severity === "CRITICAL" ? "bad" : item.severity === "ERROR" || item.severity === "HIGH" ? "warn" : "muted"}>{item.severity}</Badge>
                    <span className="font-mono text-xs">{item.code}</span>
                    <span className="text-slate-600 dark:text-slate-400">{item.message}</span>
                  </li>
                ))}
              </ul>
            )}
            <Muted className="mt-1 text-xs">
              Engines: {f.analysis.engines.map((engine) => `${engine.engine ?? "?"} ${engine.version ?? ""}${engine.databaseVersion ? ` (db ${engine.databaseVersion})` : ""}`.trim()).join(" · ") || "—"}
            </Muted>
          </section>
        )}
        {f?.signature && f.signature.signatureCount > 0 && (
          <section>
            <h3 className="mb-1 font-medium">Digital signatures: {f.signature.state}</h3>
            <ul className="space-y-1">
              {f.signature.signatures.map((signature, index) => (
                <li key={index}>{signature.integrity} · {signature.cryptographicValidity} · {signature.trust} · {signature.revocation} · {signature.modificationLevel}
                  {signature.signerSubject && <span className="block text-xs text-slate-500">Signer: {signature.signerSubject}</span>}</li>
              ))}
            </ul>
          </section>
        )}
        {f && (f.cdr.length > 0 || f.validation.length > 0) && (
          <section>
            <h3 className="mb-1 font-medium">Rebuild and final checks</h3>
            <ul className="space-y-1" data-testid="checks">
              {f.cdr.map((item) => <li key={`c${item.attempt}`}>Rebuild attempt {item.attempt}: {item.status}{item.failureCode ? ` (${item.failureCode})` : ""}{item.durationMs ? ` · ${(item.durationMs / 1000).toFixed(1)} s` : ""}</li>)}
              {f.validation.map((item) => <li key={`v${item.attempt}`}>Final check {item.attempt} ({item.artifact === "EMBEDDED_METADATA" ? "with metadata" : "rebuilt PDF"}): {item.verdict}{item.failureCodes.length > 0 && <span className="ml-2 font-mono text-xs">{item.failureCodes.join(", ")}</span>}</li>)}
            </ul>
          </section>
        )}
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
          <Row label="Detection" value={scan.detection} />
          <Row label="Policy" value={scan.policy} />
          <Row label="Uploaded" value={formatDate(scan.createdAt)} />
          {doc && <Row label="Archived" value={formatDate(doc.publishedAt)} />}
          {sidecar?.organization.reason && <Row label="Filing" value={sidecar.organization.reason} />}
          {sidecar && <Row label="Metadata revision" value={sidecar.metadataRevision} />}
          <Row label="Scan ID" value={<code className="text-xs">{scan.scanId}</code>} />
          {documentId && <Row label="Document ID" value={<code className="text-xs">{documentId}</code>} />}
          <Row label="SHA-256 of the original" value={<code className="break-all text-xs">{scan.sourceSha256}</code>} />
          {doc && <Row label="SHA-256 of the clean copy" value={<code className="break-all text-xs">{doc.sha256}</code>} />}
        </dl>
        {f && <ul className="list-disc pl-5 text-xs text-slate-500 dark:text-slate-400">{f.limitations.map((text) => <li key={text}>{text}</li>)}</ul>}
        {documentId && <a className="text-xs underline" href={`/api/v1/archive/${encodeURIComponent(documentId)}/sidecar`} download>Download the metadata file</a>}
      </div>
    </Details>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return <div className="min-w-0"><dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt><dd className="break-words">{value}</dd></div>;
}
