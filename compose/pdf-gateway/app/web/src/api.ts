import { z } from "zod";

// Every response is parsed with a schema before use; anything unexpected is an
// error, never rendered. All values are rendered as text by React (no HTML).

export const Session = z.object({
  username: z.string(),
  role: z.enum(["ADMIN", "USER", "READ_ONLY"]),
  csrfToken: z.string().regex(/^[0-9a-f]{64}$/),
  expiresAt: z.string(),
});
export type Session = z.infer<typeof Session>;

export const SetupState = z.object({ needed: z.boolean(), suggestedUsername: z.string().nullable() });

const Role = z.enum(["ADMIN", "USER", "READ_ONLY", "ENRICHMENT"]);
export type Role = z.infer<typeof Role>;
const Key = z.object({ id: z.string(), label: z.string(), createdAt: z.string(), lastUsedAt: z.string().nullable() });
export const Account = z.object({
  id: z.string(), username: z.string(), role: Role, enabled: z.boolean(), signsIn: z.boolean(),
  createdAt: z.string(), lastLoginAt: z.string().nullable(), keys: z.array(Key),
});
export type Account = z.infer<typeof Account>;
export const NewKey = z.object({ id: z.string(), label: z.string(), token: z.string(), account: Account });

export const Scan = z.object({
  scanId: z.string(),
  displayName: z.string(),
  status: z.enum(["RECEIVED", "QUARANTINED", "QUEUED", "SCANNING", "ANALYZING", "AWAITING_POLICY", "SANITIZING",
    "VALIDATING_OUTPUT", "ARCHIVING", "READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"]),
  stage: z.string(),
  detection: z.enum(["NO_THREATS_DETECTED", "SUSPICIOUS", "MALICIOUS", "UNKNOWN"]),
  policy: z.enum(["ALLOW_SANITIZED", "REVIEW_REQUIRED", "BLOCK", "CDR_ELIGIBLE"]),
  artifact: z.enum(["ORIGINAL_QUARANTINED", "SANITIZED_STAGED", "FINAL_VALIDATED", "PUBLISHED"]),
  originalRetention: z.enum(["STANDARD", "SIGNED_RESTRICTED"]),
  importStatus: z.string(),
  sourceBytes: z.number(),
  sourceSha256: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
});
export type Scan = z.infer<typeof Scan>;
export const ScanPage = z.object({ items: z.array(Scan), nextCursor: z.string().nullable() });

const Finding = z.object({ engine: z.string().nullish(), code: z.string().nullish(), severity: z.string().nullish(), message: z.string().nullish() });
export const Findings = z.object({
  scanId: z.string(),
  analysis: z.object({
    detection: z.string().nullish(), policyDecision: z.string().nullish(), policyVersion: z.string().nullish(),
    engines: z.array(z.object({ engine: z.string().nullish(), status: z.string().nullish(), version: z.string().nullish(),
      databaseVersion: z.string().nullish(), databaseDate: z.string().nullish() })),
    findings: z.array(Finding),
    textExtraction: z.object({ status: z.string().nullish(), pages: z.number().nullish(), characters: z.number().nullish() }),
  }).nullable(),
  signature: z.object({
    state: z.string(), signatureCount: z.number(), policyDecision: z.string(),
    signatures: z.array(z.object({ integrity: z.string().nullish(), cryptographicValidity: z.string().nullish(),
      trust: z.string().nullish(), revocation: z.string().nullish(), modificationLevel: z.string().nullish(),
      signerSubject: z.string().nullish(), signerIssuer: z.string().nullish() })),
    limitations: z.array(z.string()),
  }).nullable(),
  cdr: z.array(z.object({ attempt: z.number(), status: z.string(), failureCode: z.string().nullish(),
    durationMs: z.number().nullish(), dangerzoneVersion: z.string().nullish() })),
  validation: z.array(z.object({ attempt: z.number(), artifact: z.string().nullish(), verdict: z.string(),
    failureCodes: z.array(z.string()), policyVersion: z.string() })),
  archive: z.object({ documentId: z.string(), category: z.string(), reviewState: z.string() }).nullable(),
  steps: z.array(z.object({ type: z.string(), status: z.string(), attempt: z.number(),
    startedAt: z.string().nullish(), finishedAt: z.string().nullish(), errorCode: z.string().nullish() })),
  limitations: z.array(z.string()),
});
export type Findings = z.infer<typeof Findings>;

export const Document = z.object({
  documentId: z.string(), title: z.string(), category: z.string(), reviewState: z.string(), archivePath: z.string(),
  currentVersion: z.number(), metadataRevision: z.number(), sha256: z.string(), bytes: z.number(),
  publishedAt: z.string(), updatedAt: z.string(), scanId: z.string().nullable().optional(),
  issuer: z.string().nullish(), documentType: z.string().nullish(), documentDate: z.string().nullish(),
  tags: z.array(z.string()).nullish(),
});
export type Document = z.infer<typeof Document>;
export const PaperlessImport = z.object({
  status: z.string(), paperlessDocumentId: z.number().nullable(), syncedRevision: z.number().nullable(),
  metadataRevision: z.number().nullable(), lastError: z.string().nullable(), paperlessUrl: z.string().nullish(),
});
export type PaperlessImport = z.infer<typeof PaperlessImport>;
export const EnrichmentJob = z.object({ kind: z.enum(["OCR", "SUGGEST"]), status: z.string(), errorCode: z.string().nullable(),
  createdAt: z.string(), completedAt: z.string().nullable(), deadlineAt: z.string().nullish(),
  applied: z.record(z.string(), z.unknown()).nullish() });
export type EnrichmentJob = z.infer<typeof EnrichmentJob>;
export const DocumentPage = z.object({ items: z.array(Document), nextCursor: z.string().nullable() });
export const SearchHit = z.object({ documentId: z.string(), snippet: z.string() });
/** What an estimate needs: usual step times here (seconds), the page count, files ahead in the queue. */
export const Progress = z.object({
  now: z.string(), pages: z.number().nullable(), ahead: z.number(),
  typical: z.record(z.string(), z.number().nullable()),
});
export type Progress = z.infer<typeof Progress>;
export const Sidecar = z.object({
  documentId: z.string(), title: z.string(), metadataRevision: z.number(), tags: z.array(z.string()).optional(),
  documentType: z.string().optional(),
  documentDate: z.object({ value: z.string(), source: z.string(), reviewState: z.string() }).optional(),
  issuer: z.object({ value: z.string(), source: z.string(), reviewState: z.string() }).optional(),
  reference: z.object({ value: z.string(), source: z.string() }).optional(),
  amount: z.object({ value: z.string(), currency: z.string(), source: z.string() }).optional(),
  titleSource: z.string().optional(),
  artifactVersion: z.number().optional(),
  embeddedMetadata: z.object({ values: z.record(z.string(), z.string()).optional() }).passthrough().optional(),
  organization: z.object({ category: z.string(), reviewState: z.string(), reason: z.string().optional(), folder: z.string().optional() }),
  signature: z.object({ originalSigned: z.boolean(), viewingCopySigned: z.literal(false), summary: z.string().optional() }),
  relativePdfPath: z.string(),
}).passthrough();
export type Sidecar = z.infer<typeof Sidecar>;
export type MetadataChanges = {
  title?: string; tags?: string[]; issuer?: string | null; documentType?: string | null; documentDate?: string | null;
  reference?: string | null; amount?: { value: string; currency: string } | null; section?: string;
};
export const Edit = z.object({ editId: z.string(), status: z.enum(["PENDING", "APPLIED", "CONFLICT", "INVALID"]),
  resultRevision: z.number().nullable(), errorCode: z.string().nullable(), statusUrl: z.string() });

export const Summary = z.object({
  scans: z.number(), last24h: z.number(), last7d: z.number(), byStatus: z.record(z.string(), z.number()),
  malicious: z.number(), quarantined: z.number(), archived: z.number(), cdrFailures: z.number(),
  readyLatencySeconds: z.object({ p50: z.number().nullable(), p95: z.number().nullable() }),
});
export const Health = z.object({
  database: z.string(), broker: z.string(), archiveReadable: z.boolean(),
  outbox: z.object({ pending: z.number(), oldestSeconds: z.number() }),
  pipeline: z.record(z.string(), z.number()),
  clamav: z.object({ databaseVersion: z.string().nullish(), databaseDate: z.string().nullish() }).nullable(),
  cdr: z.object({ lastStatus: z.string().nullish(), lastFailureCode: z.string().nullish(), lastAt: z.string().nullish() }).nullable(),
  limitations: z.array(z.string()),
});

export const Ai = z.object({
  enabled: z.boolean(), state: z.string(), agent: z.string().nullable(), model: z.string().nullable(),
  ocrReady: z.boolean().nullable(), lastSeenAt: z.string().nullable(), lastSuccessAt: z.string().nullable(),
  reason: z.string().nullable(), queue: z.object({ waiting: z.number(), running: z.number(), failed: z.number() }),
});
const Evidenced = z.object({ value: z.string(), evidence: z.string() });
export const Suggestion = z.object({
  id: z.string(), model: z.string(), promptVersion: z.string(), status: z.enum(["PROPOSED", "APPLIED", "DISMISSED"]), createdAt: z.string(),
  fields: z.object({ title: Evidenced.optional(), issuer: Evidenced.optional(), documentType: Evidenced.optional(),
    documentDate: Evidenced.optional(), tags: z.array(z.string()).optional(), dropped: z.array(z.string()).optional() }),
});
export type Suggestion = z.infer<typeof Suggestion>;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public location: string | null = null) { super(message); }
}

let csrfToken: string | null = null;
export function setCsrf(token: string | null) { csrfToken = token; }

async function request<T>(schema: z.ZodType<T>, path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store", redirect: "error" });
  if (!response.ok) {
    let code = "HTTP_" + response.status;
    let detail = response.statusText;
    try {
      const problem = await response.json();
      if (typeof problem?.code === "string") code = problem.code;
      if (typeof problem?.detail === "string") detail = problem.detail;
    } catch { /* not a problem document */ }
    throw new ApiError(response.status, code, detail);
  }
  if (response.status === 204) return schema.parse(undefined);
  return schema.parse(await response.json());
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export const api = {
  session: () => request(Session, "/api/v1/session"),
  login: (username: string, password: string) => request(Session, "/api/v1/session", { method: "POST", ...json({ username, password }) }),
  setupState: () => request(SetupState, "/api/v1/session/setup"),
  setup: (code: string, username: string, password: string) =>
    request(Session, "/api/v1/session/setup", { method: "POST", ...json({ code, username, password }) }),
  changePassword: (current: string, next: string) =>
    request(z.undefined(), "/api/v1/session/password", { method: "PUT", ...json({ current, next }) }),
  accounts: () => request(z.array(Account), "/api/v1/accounts"),
  createAccount: (body: { username: string; role: Role; password?: string }) =>
    request(Account, "/api/v1/accounts", { method: "POST", ...json(body) }),
  updateAccount: (id: string, body: { role?: Role; enabled?: boolean; password?: string }) =>
    request(Account, `/api/v1/accounts/${encodeURIComponent(id)}`, { method: "PATCH", ...json(body) }),
  addKey: (id: string, label: string) => request(NewKey, `/api/v1/accounts/${encodeURIComponent(id)}/keys`, { method: "POST", ...json({ label }) }),
  revokeKey: (id: string, keyId: string) =>
    request(z.undefined(), `/api/v1/accounts/${encodeURIComponent(id)}/keys/${encodeURIComponent(keyId)}`, { method: "DELETE" }),
  logout: () => request(z.undefined(), "/api/v1/session", { method: "DELETE" }),
  scans: (cursor?: string) => request(ScanPage, `/api/v1/scans?limit=25${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  scan: (id: string) => request(Scan, `/api/v1/scans/${encodeURIComponent(id)}`),
  findings: (id: string) => request(Findings, `/api/v1/scans/${encodeURIComponent(id)}/findings`),
  quarantine: () => request(ScanPage, "/api/v1/quarantine?limit=100"),
  deleteQuarantined: (id: string, confirmSigned: boolean) =>
    request(z.undefined(), `/api/v1/quarantine/${encodeURIComponent(id)}?confirmSigned=${confirmSigned}`, { method: "DELETE" }),
  documents: (cursor?: string) => request(DocumentPage, `/api/v1/archive?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  /** Every document (a personal archive: a few pages of 100). */
  async allDocuments(): Promise<Document[]> {
    const items: Document[] = [];
    let cursor: string | undefined;
    for (let page = 0; page < 50; page += 1) {
      const result = await api.documents(cursor);
      items.push(...result.items);
      if (!result.nextCursor) break;
      cursor = result.nextCursor;
    }
    return items;
  },
  search: (q: string) => request(z.array(SearchHit), `/api/v1/archive/search?q=${encodeURIComponent(q)}`),
  progress: (scanId: string) => request(Progress, `/api/v1/scans/${encodeURIComponent(scanId)}/progress`),
  document: (id: string) => request(Document, `/api/v1/archive/${encodeURIComponent(id)}`),
  paperless: (scanId: string) => request(PaperlessImport, `/api/v1/scans/${encodeURIComponent(scanId)}/paperless-import`),
  sendToPaperless: (scanId: string) =>
    request(PaperlessImport, `/api/v1/scans/${encodeURIComponent(scanId)}/paperless-import`, { method: "POST" }),
  async sidecar(id: string): Promise<{ sidecar: Sidecar; etag: string }> {
    const response = await fetch(`/api/v1/archive/${encodeURIComponent(id)}/metadata`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new ApiError(response.status, "HTTP_" + response.status, response.statusText);
    return { sidecar: Sidecar.parse(await response.json()), etag: response.headers.get("ETag") ?? "" };
  },
  editMetadata: (id: string, etag: string, changes: MetadataChanges) =>
    request(Edit, `/api/v1/archive/${encodeURIComponent(id)}/metadata`, { method: "PATCH", ...json(changes), headers: { "Content-Type": "application/json", "If-Match": etag } }),
  edit: (url: string) => request(Edit, url),
  summary: () => request(Summary, "/api/v1/dashboard/summary"),
  health: () => request(Health, "/api/v1/system/health"),
  ai: () => request(Ai, "/api/v1/system/ai"),
  suggestions: (id: string) => request(z.array(Suggestion), `/api/v1/archive/${encodeURIComponent(id)}/suggestions`),
  enrichment: (id: string) => request(z.array(EnrichmentJob), `/api/v1/archive/${encodeURIComponent(id)}/enrichment`),
  scanEnrichment: (scanId: string) => request(z.array(EnrichmentJob), `/api/v1/scans/${encodeURIComponent(scanId)}/enrichment`),
  applySuggestion: (id: string, suggestion: string, etag: string) =>
    request(Edit, `/api/v1/archive/${encodeURIComponent(id)}/suggestions/${encodeURIComponent(suggestion)}/apply`, { method: "POST", headers: { "If-Match": etag } }),
  refile: (id: string) => request(z.object({ status: z.string() }), `/api/v1/archive/${encodeURIComponent(id)}/refile`, { method: "POST" }),
  deleteEverywhere: (scanId: string, confirmSigned: boolean) =>
    request(z.undefined(), `/api/v1/scans/${encodeURIComponent(scanId)}?confirmSigned=${confirmSigned}`, { method: "DELETE" }),
  dismissSuggestion: (id: string, suggestion: string) =>
    request(z.undefined(), `/api/v1/archive/${encodeURIComponent(id)}/suggestions/${encodeURIComponent(suggestion)}/dismiss`, { method: "POST" }),
};

/** Upload with progress (fetch has no upload progress). The original is never read back by the UI. */
export function upload(file: File, intent: string, onProgress: (fraction: number) => void): Promise<{ scanId: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/scans");
    xhr.withCredentials = true;
    xhr.setRequestHeader("Idempotency-Key", `web-${crypto.randomUUID()}`);
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total); };
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText);
        if (xhr.status === 202 && typeof body?.scanId === "string") resolve({ scanId: body.scanId });
        else reject(new ApiError(xhr.status, body?.code ?? "HTTP_" + xhr.status, body?.detail ?? "Upload refused", xhr.getResponseHeader("Location")));
      } catch { reject(new ApiError(xhr.status, "HTTP_" + xhr.status, "Upload refused")); }
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK", "Network error during upload"));
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("intent", intent);
    form.append("cdrMode", "RASTER");
    xhr.send(form);
  });
}

/** The first-page picture of a document's current version (rendered by the gateway from the clean copy). */
export function previewUrl(documentId: string, version: number, size: "small" | "large") {
  return `/api/v1/archive/${encodeURIComponent(documentId)}/preview?size=${size}&v=${version}`;
}
