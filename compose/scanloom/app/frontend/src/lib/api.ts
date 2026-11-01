/** The Scanloom API (/api/v1). */
export type Permission = "scan" | "everyone" | "settings" | "admin";
export type Me = { kind: string; id: string; name: string; permissions: Permission[] };
export type AuthState = { setup: boolean; me: Me | null; permissions: { id: Permission; label: string; description: string }[] };
export type Session = { id: string; current: boolean; address?: string | null; host?: string | null; agent?: string | null; created_at: number; last_seen_at: number; expires_at: number };
export type Account = { me: Me; username?: string; sessions: Session[]; password_changed_at?: number | null };
export type User = { id: string; username: string; name: string; permissions: Permission[]; enabled: boolean; created_at: number; last_login_at?: number | null };
export type UserIn = { username: string; name: string; password: string; permissions: Permission[]; enabled?: boolean };
export type UserPatch = Partial<Pick<User, "name" | "permissions" | "enabled">>;
export type ApiKey = { id: string; name: string; prefix: string; permissions: Permission[]; created_at: number; last_used_at?: number | null; last_address?: string | null; last_host?: string | null };
export type ApiKeyMade = ApiKey & { key: string };

export type Profile = "faithful" | "paper" | "mono";
export type Appearance = "auto" | "color" | "gray" | "mono";
export type Stage = "queued" | "scanning" | "decoding" | "detecting" | "rendering" | "ready" | "blocked" | "failed";
export type Point = [number, number];

export type Page = {
  id: string; position: number; original_name: string; status: Stage; error: string | null; version: number;
  width: number | null; height: number | null; corners: Point[] | null; rotation: number; appearance: Appearance;
  corner_radius: number; confidence: number | null; warnings: string[]; timings: Record<string, number>;
  detection_method: string | null; stage_started_at: string | null; source_bytes: number; result_bytes: number | null;
  jpeg_bytes: number | null; reused_from: { project: string; title: string; page: number } | null;
  thumb_url: string | null; preview_url: string | null; source_url: string | null; png_url: string | null; jpeg_url: string | null;
};
export type Progress = {
  by_stage: Partial<Record<Stage, number>>; remaining: number; ahead: number; average_seconds: number; measured: boolean;
  eta_seconds: number; current: { id: string; position: number; original_name: string; status: Stage; stage_started_at: string | null } | null;
};
export type ExportJob = { id: string; kind: "zip" | "pdf"; status: "queued" | "building" | "ready" | "failed"; filename: string; bytes: number | null; error: string | null; page_count: number; download_url: string | null };
export type Project = {
  public_id: string; title: string; profile: Profile; status: "empty" | "queued" | "processing" | "ready" | "failed";
  page_count: number; completed_count: number; failed_count: number; warning_count: number; error: string | null;
  expires_at: string | null; created_at: string; bytes: number; progress: Progress | null; exports: ExportJob[]; owner: string | null;
};
export type ProjectSummary = Pick<Project, "public_id" | "title" | "profile" | "status" | "page_count" | "completed_count" | "failed_count" | "warning_count" | "created_at"> & { thumbnail_url: string | null };
export type PageList = { items: Page[]; total: number; counts: { total: number; review: number; failed: number }; limit: number; offset: number };
export type ExportRequest = { kind: "zip" | "pdf"; quality: "lossless" | "balanced" | "small"; page_ids?: string[]; page_from?: number; page_to?: number };
export type Settings = { revision: number; values: { retention_days: number; export_ttl_minutes: number; max_files_per_project: number; max_upload_bytes: number; max_pixels: number; jpeg_quality: number; default_profile: Profile; worker_max_attempts: number } };
export type LogLine = { seq: number; at: string; level: string; logger: string; message: string; detail: string | null };
export type PageEdit = { corners?: Point[]; rotate_by?: number; appearance?: Appearance; corner_radius?: number };

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Fired when the server says nobody is signed in (401): the sign-in screen comes back. */
export const SIGNED_OUT = "scanloom:signed-out";
export const PAGE_HEADER = "X-Scanloom-Page";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        // The page's own changes say so: what another site's page can't (batlab_common/web.py).
        [PAGE_HEADER]: "1",
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach Scanloom. Check that you're on the home network.", 0);
  }
  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON: a proxy's error page
    }
    if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
    throw new ApiError(detail ?? `The server answered ${response.status}.`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const scan = (id: string) => `/projects/${encodeURIComponent(id)}`;

export const api = {
  health: () => request<{ ok: boolean; version: string }>("/health"),
  projects: (params: { limit: number; offset: number; q?: string; status?: string }) => {
    const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.q) query.set("q", params.q);
    if (params.status) query.set("status", params.status);
    return request<{ items: ProjectSummary[]; total: number }>(`/projects?${query}`);
  },
  createProject: (body: { title?: string; profile?: Profile }) => request<Project>("/projects", json("POST", body)),
  project: (id: string) => request<Project>(scan(id)),
  editProject: (id: string, body: { title?: string; profile?: Profile; page_order?: string[] }) => request<Project>(scan(id), json("PATCH", body)),
  deleteProject: (id: string) => request<void>(scan(id), { method: "DELETE" }),
  pages: (id: string, limit: number, offset: number, only?: string) =>
    request<PageList>(`${scan(id)}/pages?limit=${limit}&offset=${offset}${only ? `&only=${only}` : ""}`),
  editPages: (id: string, pageIds: string[], body: PageEdit) => request<Project>(`${scan(id)}/pages`, json("PATCH", { page_ids: pageIds, ...body })),
  deletePages: (id: string, pageIds: string[]) => request<Project>(`${scan(id)}/pages/delete`, json("POST", { page_ids: pageIds })),
  retryPages: (id: string, pageIds?: string[]) => request<Project>(`${scan(id)}/pages/retry`, json("POST", { page_ids: pageIds ?? null })),
  createExport: (id: string, body: ExportRequest) => request<{ id: string; status: string; download_url: string; page_count: number }>(`${scan(id)}/exports`, json("POST", body)),
  settings: () => request<Settings>("/settings"),
  saveSettings: (values: Settings["values"], revision: number) => request<Settings>("/settings", json("PUT", { values, revision })),
  logs: () => request<{ lines: LogLine[] }>("/system/logs?limit=200"),

  authState: () => request<AuthState>("/auth/state"),
  setup: (body: { code: string; username: string; name: string; password: string }) => request<Me>("/auth/setup", json("POST", body)),
  login: (username: string, password: string) => request<Me>("/auth/login", json("POST", { username, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  account: () => request<Account>("/auth/me"),
  changePassword: (current: string, next: string) => request<void>("/auth/password", json("PUT", { current, new: next })),
  signOutElsewhere: () => request<Account>("/auth/sessions", { method: "DELETE" }),
  users: () => request<{ users: User[] }>("/users"),
  createUser: (body: UserIn) => request<User>("/users", json("POST", body)),
  updateUser: (id: string, body: UserPatch) => request<User>(`/users/${encodeURIComponent(id)}`, json("PATCH", body)),
  resetPassword: (id: string, password: string) => request<void>(`/users/${encodeURIComponent(id)}/password`, json("PUT", { password })),
  deleteUser: (id: string) => request<void>(`/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
  keys: () => request<{ keys: ApiKey[] }>("/keys"),
  createKey: (name: string, permissions: Permission[]) => request<ApiKeyMade>("/keys", json("POST", { name, permissions })),
  updateKey: (id: string, permissions: Permission[]) => request<ApiKey>(`/keys/${encodeURIComponent(id)}`, json("PATCH", { permissions })),
  deleteKey: (id: string) => request<void>(`/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

export function errorText(caught: unknown, fallback = "Something went wrong."): string {
  return caught instanceof ApiError ? caught.message : fallback;
}
