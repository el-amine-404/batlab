/**
 * The stanzarr API (/api/v1). The types come from its OpenAPI description
 * (schema.d.ts, made by `npm run gen:api`): change the backend's
 * api/schemas.py, regenerate, and TypeScript shows what to update here.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type SongRow = Schemas["SongRow"];
export type SongList = Schemas["SongList"];
export type LyricsStatus = SongRow["status"];
export type SystemStatus = Schemas["SystemStatus"];
export type Coverage = Schemas["Coverage"];
export type Job = Schemas["Job"];
export type Worker = Schemas["Worker"];
export type WorkerList = Schemas["WorkerList"];
export type WorkerMade = Schemas["WorkerMade"];
export type Source = Schemas["Source"];
export type SourceIn = Schemas["SourceIn"];
export type SourceTypes = Schemas["SourceTypes"];
export type Notification = Schemas["Notification"];
export type NotificationIn = Schemas["NotificationIn"];
export type NotificationTypes = Schemas["NotificationTypes"];
export type FormField = Schemas["FormField"];
export type TestResult = Schemas["TestResult"];
export type Settings = Schemas["Settings"];
export type SettingsPatch = Schemas["SettingsPatch"];
export type Me = Schemas["Me"];
export type Permission = Me["permissions"][number];
export type AuthState = Schemas["AuthState"];
export type Account = Schemas["Account"];
export type User = Schemas["User"];
export type UserIn = Schemas["UserIn"];
export type UserPatch = Schemas["UserPatch"];
export type ApiKey = Schemas["ApiKey"];
export type ApiKeyMade = Schemas["ApiKeyMade"];

/** A lyrics file of a song, as GET /songs/{id} gives it. */
export type LyricsFile = {
  id: number;
  path: string;
  name: string;
  format: string;
  synced: boolean;
  language: string;
  language_name: string;
  source: string;
  ai_state: string | null;
  lines: number;
  unsure: number;
  checked: number;
};

export type ActivityEvent = {
  id: number; at: number; kind: string; song_id: number | null; message: string;
  actor: string; ok: boolean; detail: string; artist?: string | null; title?: string | null;
};
export type ActivityLog = { total: number; page: number; size: number; failed_today: number; events: ActivityEvent[] };
export type ActivityNow = {
  busy: string;
  progress: { seen?: number; folder?: string; done?: number; total?: number };
  last_scan: { added?: number; changed?: number; removed?: number; at?: number };
  jobs: { id: number; song_id: number; status: string; stage: string; worker: string; created_at: number;
          started_at: number | null; artist: string; title: string }[];
  workers_online: { name: string; gpu: string }[];
};

export type Facet = { value: string; songs: number; label?: string; artist?: string };
export type Facets = { languages: Facet[]; formats: Facet[]; made_by: Facet[]; artists: Facet[]; albums: Facet[] };
export type CoverageDay = { day: string; counts: Record<LyricsStatus, number> };

/** One answer of Find lyrics: the library's, or a source's. */
export type FindAnswer = {
  found: boolean; synced: boolean; instrumental?: boolean; lrc?: string | null; text?: string | null;
  from: "library" | "source" | "none"; source?: string | null; label: string; song_id?: number; error?: string;
};
export type FindResult = {
  artist: string; title: string;
  resolved: { artist: string; title: string; duration: number; via: string } | null;
  answers: FindAnswer[];
};

export type SongDetail = {
  song: { id: number; artist: string; album: string; title: string; duration: number; rel: string; root: string;
          path: string; added_at: number; status: LyricsStatus; isrc: string; mbid: string };
  files: LyricsFile[];
  attempt: { at: number; result: string; source: string } | null;
  jobs: { id: number; status: string; stage: string; error: string; created_at: number; finished_at: number | null }[];
  events: ActivityEvent[];
  has_voice: boolean;
  has_text: boolean;
  /** Where its best lyrics stand on the quality ladder, and the cutoff (Settings, General). */
  quality: { rank: number; cutoff: number };
  /** Why AI sync or asking the sources can't be done now; null: they can. */
  ai_blocked: string | null;
  search_blocked: string | null;
};

export type EditorLine = { t: number; s: string; u?: boolean; fixed?: boolean };
export type EditorData = {
  lines: EditorLine[];
  origin: string;
  ai_state?: string | null;
  song: { id: number; artist: string; album: string; title: string; duration: number; rel: string; root: string };
  has_voice: boolean;
  read_only: boolean;
};

export type Stats = {
  by_language: { code: string; name: string; songs: number }[];
  by_format: { format: string; synced: boolean; files: number }[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Fired when the server says nobody is signed in (401): the sign-in screen comes back. */
export const SIGNED_OUT = "stanzarr:signed-out";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        // The page's own changes say so: what another site's page can't (batlab_common/web.py).
        "X-Stanzarr-Page": "1",
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach stanzarr. Check that you're on the home network.", 0);
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

export type SortKey = "artist" | "title" | "album" | "duration" | "added" | "status";

/** What the library is narrowed to, and how it is ordered. */
export type SongFilters = {
  status?: LyricsStatus[];
  q?: string;
  language_code?: string;
  format?: string;
  made_by?: string;
  artist?: string;
  album?: string;
  to_check?: boolean;
  sort?: SortKey;
  direction?: "asc" | "desc";
};

type Param = string | number | boolean | undefined | string[];

function query(params: Record<string, Param>): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((one) => out.append(key, one));
    else if (value !== undefined && value !== "" && value !== false) out.set(key, String(value));
  }
  return out.toString();
}

export type ActivityFilters = { kind?: string; ok?: boolean; q?: string; song_id?: number };

export const api = {
  songs: (filters: SongFilters, page: number, size: number, signal?: AbortSignal) =>
    request<SongList>(`/songs?${query({ ...filters, page, size })}`, { signal }),
  facets: () => request<Facets>("/songs/facets"),
  history: (days = 90) => request<CoverageDay[]>(`/songs/history?days=${days}`),
  coverUrl: (id: number) => `/api/v1/songs/${id}/cover`,
  fileUrl: (id: number, file: number) => `/api/v1/songs/${id}/files/${file}`,
  fileLines: (id: number, file: number) => request<{ synced: boolean; lines: { t: number; s: string }[] }>(`/songs/${id}/files/${file}/lines`),
  find: (text: string) => request<FindResult>("/find", json("POST", { query: text })),
  activity: (filters: ActivityFilters, page: number, size = 50) =>
    request<ActivityLog>(`/activity?${query({ ...filters, ok: filters.ok === undefined ? undefined : String(filters.ok), page, size })}`),
  activityNow: () => request<ActivityNow>("/activity/now"),
  song: (id: number) => request<SongDetail>(`/songs/${id}`),
  searchSong: (id: number) => request<{ result: string }>(`/songs/${id}/search`, { method: "POST" }),
  aiSync: (id: number) => request<{ job: number }>(`/songs/${id}/ai-sync`, { method: "POST" }),
  editor: (id: number) => request<EditorData>(`/songs/${id}/editor`),
  saveLyrics: (id: number, lines: EditorLine[]) => request<{ saved: number }>(`/songs/${id}/lyrics`, json("PUT", { lines })),
  audioUrl: (id: number, kind: "mix" | "voice") => `/api/v1/songs/${id}/audio/${kind}`,
  peaks: (id: number, kind: "mix" | "voice") => request<{ rate: number; peaks: number[] }>(`/songs/${id}/peaks/${kind}`),

  jobs: () => request<Job[]>("/jobs"),
  cancelJob: (id: number) => request<void>(`/jobs/${id}`, { method: "DELETE" }),
  retryJob: (id: number) => request<void>(`/jobs/${id}/retry`, { method: "POST" }),
  workers: () => request<WorkerList>("/workers"),
  addWorker: (name: string) => request<WorkerMade>("/workers", json("POST", { name })),
  renameWorker: (id: string, name: string) => request<void>(`/workers/${encodeURIComponent(id)}`, json("PATCH", { name })),
  removeWorker: (id: string) => request<void>(`/workers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  retireSharedKey: () => request<void>("/workers-shared-key", { method: "DELETE" }),

  sourceTypes: () => request<SourceTypes>("/sources/types"),
  sources: () => request<{ items: Source[] }>("/sources"),
  createSource: (body: SourceIn) => request<Source>("/sources", json("POST", body)),
  updateSource: (id: string, body: SourceIn) => request<Source>(`/sources/${id}`, json("PUT", body)),
  deleteSource: (id: string) => request<void>(`/sources/${id}`, { method: "DELETE" }),
  orderSources: (ids: string[]) => request<{ items: Source[] }>("/sources/order", json("PUT", { ids })),
  testSource: (body: SourceIn, id?: string) => request<TestResult>(id ? `/sources/${id}/test` : "/sources/test", json("POST", body)),

  notificationTypes: () => request<NotificationTypes>("/notifications/types"),
  notifications: () => request<{ items: Notification[] }>("/notifications"),
  createNotification: (body: NotificationIn) => request<Notification>("/notifications", json("POST", body)),
  updateNotification: (id: string, body: NotificationIn) => request<Notification>(`/notifications/${id}`, json("PUT", body)),
  deleteNotification: (id: string) => request<void>(`/notifications/${id}`, { method: "DELETE" }),
  /** Without an id: one not saved yet. With one: its saved secrets fill the gaps. */
  testNotification: (body: NotificationIn, id?: string) =>
    request<TestResult>(id ? `/notifications/${id}/test` : "/notifications/test", json("POST", body)),

  settings: () => request<Settings>("/settings"),
  saveSettings: (patch: SettingsPatch) => request<Settings>("/settings", json("PATCH", patch)),
  status: () => request<SystemStatus>("/system/status"),
  stats: () => request<Stats>("/system/stats"),
  library: () => request<{ folders: string[]; read_only: boolean }>("/system/library"),
  events: (limit = 50) => request<ActivityEvent[]>(`/events?limit=${limit}`),
  scan: () => request<void>("/library/scan", { method: "POST" }),

  authState: () => request<AuthState>("/auth/state"),
  setup: (body: Schemas["SetupIn"]) => request<Me>("/auth/setup", json("POST", body)),
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

/** The message of anything thrown by api.*, for a toast. */
export function errorText(caught: unknown, fallback = "Something went wrong."): string {
  return caught instanceof ApiError ? caught.message : fallback;
}
