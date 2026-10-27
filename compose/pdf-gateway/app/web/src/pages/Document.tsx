import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Bot, Check, Download, ExternalLink, Eye, FileSearch, Pencil, RefreshCw, Sparkles, Trash2, Undo2, WifiOff, Wrench, X,
} from "lucide-react";
import { AlertDialog } from "radix-ui";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { api, ApiError, Scan, type EnrichmentJob, type Findings, type MetadataChanges, type PaperlessImport, type Suggestion } from "../api";
import { useSession } from "../App";
import { AiSetup, aiUp } from "../components/ai";
import { Preview, ProgressTrack, useScanProgress } from "../components/progress";
import {
  Badge, Button, Card, Details, ErrorNote, Field, Muted, Notice, Select, SelectField, Spinner, Steps, TERMINAL, buttonVariants, cx, formatBytes,
  formatDate, formatWhen, inputClass, type StepState,
} from "../components/ui";
import { AI_STATE, FIELD_LABEL, TYPE_LABEL, safetySteps, verdict } from "../words";
import { folderOf } from "./Documents";

const TAG = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const MOVING = new Set(["WAITING_FOR_AI", "LEASED", "SUBMITTED"]);
const failed = (error: unknown) => toast.error(error instanceof Error ? error.message : "That didn't work.");

/** One page per uploaded file: what happened to it, and the clean copy it became. */
export function DocumentPage() {
  const session = useSession();
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
  // While a saved change is being written into a new version of the PDF, look again until it is there.
  const meta = useQuery({ queryKey: ["sidecar", documentId], queryFn: () => api.sidecar(documentId!), enabled: ready,
    refetchInterval: (query) => query.state.data && pdfBehind(query.state.data.sidecar) ? 3_000 : false });
  const paperless = useQuery({
    queryKey: ["paperless", scanId], queryFn: () => api.paperless(scanId), enabled: !!scan.data,
    refetchInterval: (query) => ["PENDING", "IMPORTING", "FAILED_RETRYABLE"].includes(query.state.data?.status ?? "") ? 3_000 : false,
  });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, staleTime: 15_000, refetchInterval: 30_000 });
  const jobs = useQuery({
    queryKey: ["enrichment", scanId], queryFn: () => api.scanEnrichment(scanId), enabled: !!scan.data,
    refetchInterval: (query) => query.state.data?.some((job) => MOVING.has(job.status)) || !TERMINAL.has(status ?? "") ? 5_000 : false,
  });
  const suggestions = useQuery({ queryKey: ["suggestions", documentId, jobs.data?.map((job) => job.status).join()], queryFn: () => api.suggestions(documentId!), enabled: ready });
  const progress = useScanProgress(scanId, scan.data);

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

  // The laptop finishing while the page is open: say so, and show what it filled in.
  const active = jobs.data?.filter((job) => MOVING.has(job.status)).at(-1) ?? null;
  const latest = jobs.data?.at(-1);
  const [wasActive, setWasActive] = useState(false);
  useEffect(() => {
    if (wasActive && !active && latest) {
      if (latest.status === "COMPLETED") toast.success("Done: the laptop read it and it's filed.", { description: latest.model ? `By ${latest.model}.` : undefined });
      else toast.error("The AI's answer couldn't be used.", { description: latest.errorCode ?? undefined });
      void Promise.all(["sidecar", "document", "documents", "suggestions"].map((key) => client.invalidateQueries({ queryKey: [key] })));
    }
    setWasActive(!!active);
  }, [active?.status, active?.kind]);
  // The new version reaching the PDF: its picture and file follow.
  const behind = meta.data ? pdfBehind(meta.data.sidecar) : false;
  const [wasBehind, setWasBehind] = useState(false);
  useEffect(() => {
    if (wasBehind && !behind) {
      toast.success("The PDF now carries the new details.");
      void client.invalidateQueries({ queryKey: ["document", documentId] });
    }
    setWasBehind(behind);
  }, [behind]);

  if (scan.error) return <ErrorNote error={scan.error instanceof ApiError && scan.error.status === 404 ? new Error("This document doesn't exist or isn't yours.") : scan.error} />;
  if (!scan.data) return <Muted className="flex items-center gap-2"><Spinner /> Loading…</Muted>;
  const view = scan.data;
  const sidecar = meta.data?.sidecar;
  const v = verdict(view);
  const reload = () => Promise.all(["sidecar", "document", "documents", "suggestions", "enrichment", "paperless"].map((key) => client.invalidateQueries({ queryKey: [key] })));

  const safety = safetySteps(view, findings.data).map((step) => step.key === "archive" && ready && doc.data
    ? { ...step, detail: `In ${folderOf(doc.data.archivePath)}.` } : step);
  const steps = [
    ...safety.slice(0, -1),
    // The AI files the document before it is saved, so its step comes first.
    ...aiStep(view, ai.data?.state, ai.data?.enabled, sidecar?.organization, jobs.data, suggestions.data),
    safety[safety.length - 1],
    paperlessStep(view, paperless.data, canEdit, async () => {
      try { await api.sendToPaperless(scanId); toast.success("Sending the clean copy to Paperless."); }
      catch (error) { failed(error); }
      await client.invalidateQueries({ queryKey: ["paperless", scanId] });
    }),
  ];
  const aiWorking = active ? (active.kind === "OCR" && active.status !== "WAITING_FOR_AI" ? "READING" : active.status) : null;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/documents" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> All documents</Link>
        <h1 className="text-2xl font-semibold tracking-tight break-words sm:text-3xl" data-testid="document-title">{sidecar?.title ?? view.displayName}</h1>
        <p className="mt-1 text-sm break-all text-muted-foreground">{view.displayName} · {formatBytes(view.sourceBytes)} · added {formatWhen(view.createdAt)}</p>
      </div>

      <Notice tone={v.tone} title={v.title}>{v.text}</Notice>

      {progress && !progress.finished && <Card><ProgressTrack value={progress} /></Card>}

      {ready && doc.data && sidecar && meta.data && (
        <CopyCard documentId={documentId!} doc={doc.data} sidecar={sidecar} etag={meta.data.etag} paperless={paperless.data}
          canEdit={canEdit} onSaved={reload} ai={ai.data} aiWorking={aiWorking}
          onRefile={async () => {
            const asked = await api.refile(documentId!);
            toast.success(asked.reading ? "Reading the pages again on the laptop, then filing it." : "Asked the AI to file it again.",
              { description: asked.reading ? "Its text came from the sandbox's quick French-only reading; the laptop reads French, English and Arabic. About a minute; this page updates by itself."
                : "It takes about a minute; this page updates by itself." });
            await client.invalidateQueries({ queryKey: ["enrichment", scanId] });
          }} />
      )}

      {ready && meta.data && suggestions.data?.some((item) => item.status === "PROPOSED") && (
        <Card title="Suggestions from the AI" icon={<Sparkles className="size-5" />}
          description="Read by a model on your laptop from this document's text. Each value shows the words it came from; nothing changes unless you use it.">
          <div className="space-y-3">
            {suggestions.data.filter((item) => item.status === "PROPOSED").map((item) => (
              <SuggestionCard key={item.id} item={item} canEdit={canEdit}
                onApply={async () => {
                  await api.applySuggestion(documentId!, item.id, meta.data!.etag);
                  toast.success("Title and tags used.");
                  setTimeout(reload, 1500);
                }}
                onDismiss={async () => { await api.dismissSuggestion(documentId!, item.id); await reload(); }} />
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="What happened" icon={<FileSearch className="size-5" />}>
          <Steps items={steps} />
        </Card>
        <div className="space-y-6">
          <TechnicalDetails scan={view} findings={findings.data} sidecar={sidecar} documentId={documentId} doc={doc.data} />
          {TERMINAL.has(view.status) && canEdit && <DeleteCard scan={view} ready={ready} />}
        </div>
      </div>
    </div>
  );
}

/** What the AI button says and whether it can be pressed, from the AI's state and this document's job. */
function aiButton(ai: { state: string; enabled: boolean } | undefined, working: string | null): { label: ReactNode; disabled: boolean; title: string } {
  if (working === "READING") return { label: <><Spinner /> Reading the pages…</>, disabled: true, title: "The laptop's OCR, then the AI files it." };
  if (working === "LEASED" || working === "SUBMITTED") return { label: <><Spinner /> The AI is reading it…</>, disabled: true, title: "About a minute." };
  if (working === "WAITING_FOR_AI") {
    return aiUp(ai?.state) ? { label: <><Spinner /> Queued for the AI…</>, disabled: true, title: "It starts within a minute." }
      : { label: <><WifiOff /> Waiting for the laptop</>, disabled: true, title: "It's asked; it runs when the AI laptop is back." };
  }
  if (!aiUp(ai?.state)) return { label: <><WifiOff /> AI offline</>, disabled: true, title: AI_STATE[ai?.state ?? ""] ?? "The AI isn't answering." };
  return { label: <><RefreshCw /> Re-run the AI</>, disabled: false, title: "Read the document again and file it." };
}

function CopyCard({ documentId, doc, sidecar, etag, paperless, canEdit, onSaved, ai, aiWorking, onRefile }: {
  documentId: string; doc: NonNullable<Awaited<ReturnType<typeof api.document>>>; sidecar: Awaited<ReturnType<typeof api.sidecar>>["sidecar"];
  etag: string; paperless?: PaperlessImport; canEdit: boolean; onSaved: () => Promise<unknown>;
  ai: Awaited<ReturnType<typeof api.ai>> | undefined; aiWorking: string | null; onRefile: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [undo, setUndo] = useState<{ changes: MetadataChanges; text: string } | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  // After a save: offer to put the previous values back (itself an ordinary edit).
  const saved = async (changes: MetadataChanges) => {
    setUndo({ changes: previous(sidecar, changes), text: Object.keys(changes).length === 1 ? `${FIELD_NAME[Object.keys(changes)[0]] ?? "Change"} saved.` : "Changes saved." });
    await onSaved();
  };
  const identity = sidecar.documentType === "identity_document" || !!sidecar.holder;
  type Row = [string, ReactNode, InlineSpec | null];
  const rows: Row[] = [
    ["Title", sidecar.title, { key: "title", value: sidecar.title, required: true }],
    ["From", sidecar.issuer?.value, { key: "issuer", value: sidecar.issuer?.value ?? "" }],
    ...(identity ? [["Holder", sidecar.holder?.value, { key: "holder", value: sidecar.holder?.value ?? "" }] as Row,
      ["Valid until", sidecar.validUntil?.value, { key: "validUntil", value: sidecar.validUntil?.value ?? "", type: "date" }] as Row] : []),
    ["Type", sidecar.documentType ? TYPE_LABEL[sidecar.documentType] ?? sidecar.documentType : undefined,
      { key: "documentType", value: sidecar.documentType ?? "", options: Object.entries(TYPE_LABEL) }],
    ["Date", sidecar.documentDate?.value, { key: "documentDate", value: sidecar.documentDate?.value ?? "", type: "date" }],
    ["Number", sidecar.reference?.value, { key: "reference", value: sidecar.reference?.value ?? "" }],
    ["Amount", sidecar.amount ? `${sidecar.amount.value} ${sidecar.amount.currency}` : undefined, null],
    ["Folder", folderOf(doc.archivePath), { key: "section", value: sidecar.organization.category, options: SECTIONS.map((value) => [value, value]), required: true }],
    ["File name", <span className="font-mono text-xs break-all" data-testid="file-name" title="It follows the title and the date: change those to rename it.">{sidecar.relativePdfPath}</span>, null],
    ["Tags", sidecar.tags?.length ? sidecar.tags.map((tag) => (
      <Link key={tag} to={`/documents?tag=${encodeURIComponent(tag)}`} title={`Every document tagged ${tag}`} data-testid="tag-link"
        className="rounded-full focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"><Badge className="hover:border-primary/50 hover:text-primary">{tag}</Badge></Link>
    )) : undefined, { key: "tags", value: (sidecar.tags ?? []).join(", "), hint: "separated by commas", links: true }],
  ];
  const state = sidecar.organization.reviewState;
  const byAi = state === "AI";
  const embedded = sidecar.embeddedMetadata?.values ?? {};
  // A saved change reaches the PDF through a new version, checked again first: show it until then.
  const updating = pdfBehind(sidecar);
  const rewritten = (sidecar.artifactVersion ?? 1) - 1;
  const file = `/api/v1/archive/${encodeURIComponent(documentId)}/file`;
  const button = aiButton(ai, aiWorking);
  const aiOff = !aiUp(ai?.state);

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card shadow-xl shadow-black/20 backdrop-blur-xl">
      <div className="grid gap-6 p-5 sm:p-6 md:grid-cols-[13rem_minmax(0,1fr)]">
        <div className="mx-auto w-full max-w-56 space-y-3 md:max-w-none">
          <a href={`${file}?view=true`} target="_blank" rel="noopener noreferrer" title="Open the clean copy" data-testid="large-preview"
            className="group relative block overflow-hidden rounded-lg ring-1 ring-border transition hover:ring-primary/60">
            <Preview documentId={documentId} version={doc.currentVersion} size="large" className="aspect-[3/4] w-full rounded-none ring-0" alt="First page" />
            <span className="absolute inset-0 flex items-center justify-center bg-background/60 opacity-0 transition group-hover:opacity-100"><Eye className="size-6" /></span>
          </a>
          <div className="grid grid-cols-2 gap-2">
            {/* View opens the browser's own PDF viewer on the clean copy (validated: no scripts, links or forms). */}
            <a className={buttonVariants({ variant: "primary", size: "sm" })} href={`${file}?view=true`} target="_blank" rel="noopener noreferrer" data-testid="view-pdf"><Eye /> View</a>
            <a className={buttonVariants({ variant: "secondary", size: "sm" })} href={file} download data-testid="download-pdf"><Download /> Download</a>
          </div>
          {paperless?.paperlessUrl && (
            <a className={cx(buttonVariants({ variant: "outline", size: "sm" }), "w-full")} href={paperless.paperlessUrl} target="_blank" rel="noopener noreferrer" data-testid="open-paperless">
              <ExternalLink /> Open in Paperless</a>
          )}
          <p className="text-center text-xs text-muted-foreground">{formatBytes(doc.bytes)}</p>
        </div>

        <div className="min-w-0">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="filed-by">
              {byAi ? <Badge tone="info"><Sparkles /> Filled in by the AI</Badge> : state === "RULE" ? <Badge tone="good"><Check /> Filed by your rules</Badge>
                : state === "VERIFIED" ? <Badge tone="good"><Check /> Filed by you</Badge> : <Badge tone="warn">Not filed yet</Badge>}
              {!editing && <span className="text-muted-foreground">
                {byAi ? "Checked against the document's text. Click a value to correct it." : state === "RULE" ? "A filing rule recognised it." : state === "VERIFIED" ? "" : "Click a value to fill it in, or re-run the AI."}
              </span>}
            </div>
            {canEdit && !editing && (
              <div className="flex flex-wrap gap-2">
                {ai?.enabled && (
                  <Button size="sm" variant="secondary" disabled={button.disabled || asking} title={button.title} data-testid="refile" data-ai={aiWorking ?? (aiOff ? "offline" : "ready")}
                    onClick={async () => { setAsking(true); try { await onRefile(); } catch (failure) { failed(failure); } finally { setAsking(false); } }}>
                    {asking ? <><Spinner /> Asking…</> : button.label}
                  </Button>
                )}
                <Button size="sm" variant="secondary" onClick={() => setEditing(true)} data-testid="edit"><Pencil /> Edit all</Button>
              </div>
            )}
          </div>

          {canEdit && ai?.enabled && aiOff && !editing && (
            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning" data-testid="ai-offline">
              <WifiOff className="size-4 shrink-0" />
              <span className="flex-1">{AI_STATE[ai.state] ?? "The AI isn't answering"}{aiWorking === "WAITING_FOR_AI" ? ": this document is queued and is filed when it's back." : "."}</span>
              <Button size="sm" variant="ghost" className="text-warning" onClick={() => setShowSetup(!showSetup)}><Wrench /> {showSetup ? "Hide" : "How to start it"}</Button>
            </div>
          )}
          {showSetup && aiOff && <div className="mb-4"><AiSetup ai={ai} /></div>}

          {undo && !editing && (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-sm" data-testid="saved-note">
              <span className="flex items-center gap-2"><Check className="size-4 text-success" /> {undo.text} The PDF and Paperless follow in a moment.</span>
              <span className="flex gap-1">
                <Button size="sm" variant="ghost" disabled={undoing} data-testid="undo" onClick={async () => {
                  setUndoing(true);
                  try { await saveEdit(documentId, etag, undo.changes); setUndo(null); toast.success("Put back."); await onSaved(); }
                  catch (failure) { failed(failure); } finally { setUndoing(false); }
                }}><Undo2 /> {undoing ? "Undoing…" : "Undo"}</Button>
                <Button size="icon-sm" variant="ghost" onClick={() => setUndo(null)} aria-label="Dismiss"><X /></Button>
              </span>
            </div>
          )}

          {editing ? <EditForm documentId={documentId} sidecar={sidecar} etag={etag} onDone={async (changes) => { setEditing(false); if (changes) await saved(changes); }} /> : (
            <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-4 gap-y-0.5 text-sm" data-testid="copy-fields">
              {rows.filter(([, value, spec]) => (value !== undefined && value !== "") || (canEdit && spec)).map(([label, value, spec]) => (
                <div key={label} className="contents">
                  <dt className="py-1.5 text-muted-foreground">{label}</dt>
                  <dd className="min-w-0 break-words">
                    {canEdit && spec
                      ? <InlineField label={label} display={value} spec={spec} documentId={documentId} etag={etag} onSaved={saved} />
                      : <span className="flex flex-wrap gap-1 py-1.5">{value}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}

          {Object.keys(embedded).length > 0 && (
            <details className="mt-5 border-t border-border pt-4 text-sm" data-testid="inside-pdf">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Inside the PDF {updating && <Badge tone="info"><Spinner /> updating</Badge>}</summary>
              <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1">
                {["Title", "Author", "Subject", "Keywords"].filter((key) => embedded[key]).map((key) => (
                  <div key={key} className="contents"><dt className="text-muted-foreground">{key}</dt><dd className="break-words">{embedded[key]}</dd></div>
                ))}
              </dl>
              <Muted className="mt-1 text-xs">
                These details are written inside the PDF too, so they travel with the file. When they change, a new copy of the PDF is
                made with them and checked again before it replaces this one; the pages themselves never change.
                {rewritten > 0 && ` Updated ${rewritten} ${rewritten === 1 ? "time" : "times"} since it was saved.`} No producer, creator or dates are written.
              </Muted>
            </details>
          )}
          <Muted className="mt-4 text-xs">
            The clean copy looks like your original: each page was redrawn as a lossless image, with an invisible text layer so you can search and copy text.
          </Muted>
        </div>
      </div>
    </section>
  );
}

/** A saved change not yet in the PDF itself (its new version is being written and checked). */
function pdfBehind(sidecar: SidecarData) {
  const embedded = sidecar.embeddedMetadata?.values ?? {};
  return Object.keys(embedded).length > 0 && embedded.Title !== plain(sidecar.title);
}

/** The same plain punctuation the archive writes into the PDF. */
function plain(text: string) {
  return text.replace(/[–—‒−·•]/g, "-").replace(/[“”„«»‘’]/g, "'")
    .replace(/…/g, "...").replace(/\s+/g, " ").trim();
}

const SECTIONS = ["Finance", "Employment", "Identity", "Medical", "Education", "Contracts", "Property", "Vehicles", "Taxes", "Personal", "Other", "Unsorted"];

type SidecarData = Awaited<ReturnType<typeof api.sidecar>>["sidecar"];
const FIELD_NAME: Record<string, string> = { title: "Title", issuer: "Correspondent", documentType: "Type", documentDate: "Date",
  reference: "Number", amount: "Amount", section: "Section", tags: "Tags" };

/** Send an edit and wait (briefly) until the archive worker applied it. */
async function saveEdit(documentId: string, etag: string, changes: MetadataChanges) {
  try {
    const edit = await api.editMetadata(documentId, etag, changes);
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const status = await api.edit(edit.statusUrl);
      if (status.status === "APPLIED") return;
      if (status.status !== "PENDING") throw new Error(status.status === "CONFLICT" ? "It was changed meanwhile; reload the page and try again." : "The change was refused.");
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error("Still saving; check again in a moment.");
  } catch (failure) {
    throw failure instanceof ApiError && failure.code === "REVISION_CONFLICT" ? new Error("It was changed meanwhile; reload the page and try again.") : failure;
  }
}

/** The values a change replaces, as a change that puts them back. */
function previous(sidecar: SidecarData, changes: MetadataChanges): MetadataChanges {
  const out: MetadataChanges = {};
  for (const key of Object.keys(changes) as (keyof MetadataChanges)[]) {
    switch (key) {
      case "title": out.title = sidecar.title; break;
      case "tags": out.tags = sidecar.tags ?? []; break;
      case "issuer": out.issuer = sidecar.issuer?.value ?? null; break;
      case "documentType": out.documentType = sidecar.documentType ?? null; break;
      case "documentDate": out.documentDate = sidecar.documentDate?.value ?? null; break;
      case "reference": out.reference = sidecar.reference?.value ?? null; break;
      case "amount": out.amount = sidecar.amount ? { value: sidecar.amount.value, currency: sidecar.amount.currency } : null; break;
      case "section": out.section = sidecar.organization.category; break;
    }
  }
  return out;
}

type InlineSpec = { key: keyof MetadataChanges; value: string; type?: string; options?: [string, string][]; required?: boolean; hint?: string;
  /** The value holds links (tags): a pencil beside it edits, instead of a click on the value. */
  links?: boolean };

/** A field that becomes an input when clicked; Enter saves, Escape cancels. */
function InlineField({ label, display, spec, documentId, etag, onSaved }: {
  label: string; display: ReactNode; spec: InlineSpec; documentId: string; etag: string; onSaved: (changes: MetadataChanges) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(spec.value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { if (!open) setValue(spec.value); }, [spec.value, open]);
  const empty = display === undefined || display === "";
  if (!open && spec.links && !empty) {
    return (
      <div className="group -mx-2 flex w-full flex-wrap items-center gap-1 rounded-md px-2 py-1.5">
        {display}
        <button type="button" onClick={() => setOpen(true)} data-testid={`inline-${spec.key}`} title={`Change ${label.toLowerCase()}`} aria-label={`Change ${label.toLowerCase()}`}
          className="ml-auto flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
          <Pencil className="size-3.5" />
        </button>
      </div>
    );
  }
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} data-testid={`inline-${spec.key}`} title={`Change ${label.toLowerCase()}`}
        className="group -mx-2 flex w-full flex-wrap items-center gap-1 rounded-md px-2 py-1.5 text-left transition hover:bg-accent">
        {empty ? <span className="text-muted-foreground/70">Add…</span> : display}
        <Pencil className="ml-auto size-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
      </button>
    );
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const text = value.trim();
    let changes: MetadataChanges;
    if (spec.key === "tags") {
      const list = [...new Set(text.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
      if (!list.every((tag) => TAG.test(tag))) { setError(new Error("Tags: lowercase letters, digits, dot, dash or underscore, separated by commas.")); return; }
      changes = { tags: list };
    } else if (spec.required && !text) { setError(new Error(`${label} can't be empty.`)); return; }
    else changes = { [spec.key]: text || null } as MetadataChanges;
    if (text === spec.value.trim()) { setOpen(false); return; }
    setBusy(true);
    try { await saveEdit(documentId, etag, changes); setOpen(false); await onSaved(changes); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }} className="space-y-1.5 py-1" data-testid={`inline-form-${spec.key}`}>
      {spec.options ? (
        <Select autoFocus className="h-9" value={value} onChange={(event) => setValue(event.target.value)} aria-label={label}>
          {!spec.required && <option value="">-</option>}
          {spec.options.map(([option, text]) => <option key={option} value={option}>{text}</option>)}
        </Select>
      ) : <input autoFocus className={cx(inputClass, "h-9")} type={spec.type ?? "text"} value={value} maxLength={200} aria-label={label} onChange={(event) => setValue(event.target.value)} />}
      {spec.hint && <span className="block text-xs text-muted-foreground">{spec.hint}</span>}
      <ErrorNote error={error} />
      <span className="flex gap-1">
        <Button type="submit" size="sm" disabled={busy}>{busy ? <><Spinner /> Saving…</> : <><Check /> Save</>}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </span>
    </form>
  );
}

function EditForm({ documentId, sidecar, etag, onDone }: {
  documentId: string; sidecar: SidecarData; etag: string; onDone: (changes: MetadataChanges | null) => void;
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
    const changes: MetadataChanges = {
      title: title.trim(), tags: [...new Set(list)], issuer: issuer.trim() || null, documentType: kind || null,
      documentDate: date || null, reference: reference.trim() || null,
      amount: amount ? { value: amount, currency: currency.toUpperCase() } : null, section,
    };
    try { await saveEdit(documentId, etag, changes); onDone(changes); }
    catch (failure) { setError(failure); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={save} className="space-y-4" data-testid="edit-form">
      <Field label="Title" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
      <Field label="From (correspondent)" value={issuer} maxLength={120} onChange={(event) => setIssuer(event.target.value)} />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField label="Type" value={kind} onChange={(event) => setKind(event.target.value)} name="documentType">
          <option value="">-</option>
          {Object.entries(TYPE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <Field label="Date" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        <Field label="Number" value={reference} maxLength={60} onChange={(event) => setReference(event.target.value)} />
        <div className="grid grid-cols-[1fr_5.5rem] gap-2">
          <Field label="Amount" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="1015.68" />
          <Field label="Currency" value={currency} maxLength={3} onChange={(event) => setCurrency(event.target.value)} placeholder="MAD" />
        </div>
        <SelectField label="Section" value={section} onChange={(event) => setSection(event.target.value)} name="section">
          {SECTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
        </SelectField>
      </div>
      <Field label="Tags" hint="Separated by commas, e.g. finance, paid" value={tags} onChange={(event) => setTags(event.target.value)} />
      <ErrorNote error={error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>{busy ? <><Spinner /> Saving…</> : "Save"}</Button>
        <Button type="button" variant="secondary" onClick={() => onDone(null)}>Cancel</Button>
      </div>
      <Muted className="text-xs">
        Saving files it again (folder and file name follow), updates Paperless, and writes a new version of the PDF
        with these values; the new version is checked again before it replaces the current one.
      </Muted>
    </form>
  );
}

function SuggestionCard({ item, canEdit, onApply, onDismiss }: { item: Suggestion; canEdit: boolean; onApply: () => Promise<void>; onDismiss: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const run = (action: () => Promise<void>) => async () => {
    setBusy(true);
    try { await action(); } catch (failure) { failed(failure); } finally { setBusy(false); }
  };
  const rows = (["title", "issuer", "documentType", "documentDate"] as const).flatMap((key) => item.fields[key] ? [[key, item.fields[key]!] as const] : []);
  return (
    <div className="rounded-lg border border-border bg-background/40 p-4 text-sm" data-testid="suggestion">
      <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground"><Bot className="size-3.5" /> {item.model} · {formatWhen(item.createdAt)}</p>
      <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2">
        {rows.map(([key, field]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{FIELD_LABEL[key]}</dt>
            <dd className="min-w-0 break-words">{field.value}<span className="block text-xs text-muted-foreground">from “{field.evidence}”</span></dd>
          </div>
        ))}
        {item.fields.tags && item.fields.tags.length > 0 && (
          <div className="contents"><dt className="text-muted-foreground">Tags</dt><dd className="flex flex-wrap gap-1">{item.fields.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}</dd></div>
        )}
      </dl>
      {canEdit && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy || (!item.fields.title && !item.fields.tags)} onClick={run(onApply)}><Check /> Use title and tags</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={run(onDismiss)}>Not now</Button>
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
  const button = (text: string) => canEdit && <Button size="sm" variant="secondary" className="mt-2" data-testid="send-to-paperless" onClick={() => { void send(); }}>{text}</Button>;
  switch (item.status) {
    case "IMPORTED":
      return { key: "paperless", label, state: "done", detail: <span data-testid="paperless-status">Added as document #{item.paperlessDocumentId}{item.paperlessUrl && <> · <a className="text-primary underline-offset-4 hover:underline" href={item.paperlessUrl} target="_blank" rel="noopener noreferrer">open it</a></>}.</span> };
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

/** Who did the reading: the model and the laptop; and where the text came from. */
function aiWho(job: EnrichmentJob, ocr: EnrichmentJob | undefined) {
  const parts = [job.model ? `${job.model}` : null, job.agent ? `on ${job.agent}` : null].filter(Boolean).join(" ");
  const text = ocr?.status === "COMPLETED" ? `text read from the page images by ${ocr.engine ?? "Tesseract"} first` : "from the document's text layer";
  return <span className="mt-1 flex items-start gap-1.5 text-xs"><Bot className="mt-px size-3.5 shrink-0" /><span>{parts || "A model on your laptop"}, {text}; a text model, no vision.{job.completedAt && ` ${formatWhen(job.completedAt)}.`}</span></span>;
}

function aiStep(scan: Scan, state: string | undefined, enabled: boolean | undefined, organization: { reviewState: string; reason?: string } | undefined,
                jobs: EnrichmentJob[] | undefined, suggestions: Suggestion[] | undefined): Step[] {
  const reviewState = organization?.reviewState;
  const job = jobs?.filter((item) => item.kind === "SUGGEST").at(-1);
  const ocr = jobs?.filter((item) => item.kind === "OCR").at(-1);
  if (reviewState === "RULE" && organization?.reason?.includes("machine-readable zone") && !(ocr && MOVING.has(ocr.status))) {
    return [{ key: "ai", label: "Read from the card's machine-readable zone", state: "done",
      detail: "Holder, number and expiry from the lines of <<< at the bottom, kept because every check digit agrees. No AI needed." }];
  }
  if (enabled === false) return [];
  const label = "Filed by the AI (on your laptop)";
  if (ocr && MOVING.has(ocr.status)) {
    return [{ key: "ai", label, state: aiUp(state) ? "running" : "waiting",
      detail: aiUp(state) ? "Reading the pages again on the laptop (French, English and Arabic), then the AI files it…"
        : `It reads the pages again when the laptop is back.${state ? ` ${AI_STATE[state] ?? state}.` : ""}` }];
  }
  const away = state && !aiUp(state) ? ` ${AI_STATE[state] ?? state}.` : "";
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
      return [{ key: "ai", label, state: aiUp(state) ? "running" : "waiting",
        detail: aiUp(state) ? "Queued; the laptop picks it up within a minute."
          : scan.status === "READY" ? `Saved without it; the AI files it when the laptop is back.${away}`
          : `Waiting for the AI laptop (up to 10 min, then it's saved as it is).${away}` }];
    case "LEASED": case "SUBMITTED":
      return [{ key: "ai", label, state: "running", detail: "The AI is reading it (about a minute)…" }];
    case "COMPLETED":
      return [{ key: "ai", label, state: "done", detail: <>{proposed ? "You had filed it already; its answer is a suggestion (above)."
        : applied ? "Title, correspondent, type, date and folder filled in from the document's text." : "Nothing in the text was certain enough to use."}{aiWho(job, ocr)}</> }];
    case "FAILED": return [{ key: "ai", label, state: "failed", detail: <>The AI's answer couldn't be used{job.errorCode ? ` (${job.errorCode})` : ""}; saved without it.{aiWho(job, ocr)}</> }];
    default: return [{ key: "ai", label, state: "skipped", detail: "Gave up waiting for the laptop." }];
  }
}

function DeleteCard({ scan, ready }: { scan: Scan; ready: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const signed = scan.originalRetention === "SIGNED_RESTRICTED";
  const what = ready ? "The clean copy in your archive and in Paperless (Paperless keeps it in its trash for a while), the original" : "The held original";
  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteEverywhere(scan.scanId, signed);
      await client.invalidateQueries({ queryKey: ["scans"] });
      await client.invalidateQueries({ queryKey: ["documents"] });
      toast.success("Deleted everywhere.");
      navigate("/documents");
    } catch (failure) { failed(failure); setBusy(false); }
  };
  return (
    <Details summary="Delete" icon={<Trash2 className="size-4" />}>
      <Muted className="mb-3">Removes it everywhere: the archived copy, the original kept in quarantine, the reports, and the Paperless copy.</Muted>
      <AlertDialog.Root>
        <AlertDialog.Trigger asChild><Button variant="danger" data-testid="delete"><Trash2 /> Delete everywhere</Button></AlertDialog.Trigger>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
          <AlertDialog.Content className="fixed top-1/2 left-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-6 shadow-2xl" data-testid="delete-dialog">
            <AlertDialog.Title className="text-lg font-semibold">Delete it everywhere?</AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted-foreground">
              {what} and every record of it go. Your own file on your computer is not touched.{signed && " The original is digitally signed and kept as evidence."}
            </AlertDialog.Description>
            <div className="mt-6 flex justify-end gap-2">
              <AlertDialog.Cancel asChild><Button variant="secondary">Keep it</Button></AlertDialog.Cancel>
              <Button variant="danger" disabled={busy} onClick={remove} data-testid="confirm-delete">{busy ? <><Spinner /> Deleting…</> : <><Trash2 /> Delete</>}</Button>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </Details>
  );
}

function TechnicalDetails({ scan, findings, sidecar, documentId, doc }: {
  scan: Scan; findings?: Findings; sidecar?: { metadataRevision: number; organization: { reason?: string } };
  documentId: string | null; doc?: { sha256: string; publishedAt: string };
}) {
  const f = findings;
  return (
    <Details summary="Technical details" icon={<Wrench className="size-4" />}>
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
                    <span className="text-muted-foreground">{item.message}</span>
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
                  {signature.signerSubject && <span className="block text-xs text-muted-foreground">Signer: {signature.signerSubject}</span>}</li>
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
          <Row label="SHA-256 of the original" value={<code className="text-xs break-all">{scan.sourceSha256}</code>} />
          {doc && <Row label="SHA-256 of the clean copy" value={<code className="text-xs break-all">{doc.sha256}</code>} />}
        </dl>
        {f && <ul className="list-disc pl-5 text-xs text-muted-foreground">{f.limitations.map((text) => <li key={text}>{text}</li>)}</ul>}
        {documentId && <a className="text-xs text-primary underline-offset-4 hover:underline" href={`/api/v1/archive/${encodeURIComponent(documentId)}/sidecar`} download>Download the metadata file</a>}
      </div>
    </Details>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return <div className="min-w-0"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words">{value}</dd></div>;
}
