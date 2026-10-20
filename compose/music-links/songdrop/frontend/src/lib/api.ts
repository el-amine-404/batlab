/**
 * The songdrop API (/api/v1). The types come from its OpenAPI description
 * (schema.d.ts, made by `npm run gen:api`): change the backend's
 * app/schemas.py, regenerate, and TypeScript shows what to update here.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Engine = "spotdl" | "ytdlp";
export type Detection = Schemas["Detection"];
export type OptionField = Schemas["OptionField"];
export type OptionsSpec = Schemas["OptionsSpec"];
export type OptionValues = Record<string, string | boolean>;
export type Job = Schemas["Job"];
export type JobStatus = Job["status"];
export type JobFile = Schemas["JobFile"];
export type MissingSong = Schemas["MissingSong"];
export type RetryState = MissingSong["state"];
export type Settings = Schemas["Settings"];
export type SettingsPatch = Schemas["SettingsPatch"];
export type CookieStatus = Schemas["CookieStatus"];
export type FolderListing = Schemas["FolderListing"];
export type FolderCheck = Schemas["FolderCheck"];
export type Connection = Schemas["Connection"];
export type ConnectionIn = Schemas["ConnectionIn"];
export type ConnectionType = Schemas["ConnectionType"];
export type ConnectionTypes = Schemas["ConnectionTypes"];
export type TestResult = Schemas["TestResult"];
export type SystemStatus = Schemas["SystemStatus"];
export type Me = Schemas["Me"];
export type Permission = Me["permissions"][number];
export type AuthState = Schemas["AuthState"];
export type Account = Schemas["Account"];
export type User = Schemas["User"];
export type UserIn = Schemas["UserIn"];
export type UserPatch = Schemas["UserPatch"];
export type ApiKey = Schemas["ApiKey"];
export type ApiKeyMade = Schemas["ApiKeyMade"];

/** Waiting to start, or downloading. */
export const ACTIVE: JobStatus[] = ["queued", "running"];
/** A missing song in a later try now. */
export const RETRYING: RetryState[] = ["queued", "retrying"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Fired when the server says nobody is signed in (401): the sign-in screen comes back. */
export const SIGNED_OUT = "songdrop:signed-out";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        // The page's own changes say so: what another site's page can't (batlab_common/web.py).
        "X-Songdrop-Page": "1",
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach songdrop. Check that you're on the home network.", 0);
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
const query = (params: Record<string, string>) => new URLSearchParams(params).toString();

export const api = {
  options: () => request<OptionsSpec>("/options"),
  detect: (url: string, signal?: AbortSignal) => request<Detection>("/detect", { ...json("POST", { url }), signal }),

  jobs: () => request<{ jobs: Job[]; retention_hours: number }>("/jobs"),
  createJob: (url: string, options: OptionValues) => request<Job>("/jobs", json("POST", { url, options })),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  removeJob: (id: string) => request<void>(`/jobs/${id}`, { method: "DELETE" }),
  retrySong: (id: string, index: number) => request<Job>(`/jobs/${id}/errors/${index}/retry`, { method: "POST" }),

  settings: () => request<Settings>("/settings"),
  saveSettings: (patch: SettingsPatch) => request<Settings>("/settings", json("PATCH", patch)),

  cookies: () => request<CookieStatus>("/cookies"),
  saveCookies: (file: File) => request<CookieStatus>("/cookies", { method: "PUT", body: file }),
  removeCookies: () => request<void>("/cookies", { method: "DELETE" }),

  folders: (path: string, signal?: AbortSignal) => request<FolderListing>(`/folders?${query({ path })}`, { signal }),
  checkFolder: (path: string, signal?: AbortSignal) =>
    request<FolderCheck>(`/folders/check?${query({ path })}`, { signal }),

  connectionTypes: () => request<ConnectionTypes>("/connections/types"),
  connections: () => request<{ connections: Connection[] }>("/connections"),
  createConnection: (body: ConnectionIn) => request<Connection>("/connections", json("POST", body)),
  updateConnection: (id: string, body: ConnectionIn) => request<Connection>(`/connections/${id}`, json("PUT", body)),
  removeConnection: (id: string) => request<void>(`/connections/${id}`, { method: "DELETE" }),
  /** Without an id: a connection not saved yet. With one: its saved secrets fill the gaps. */
  testConnection: (body: ConnectionIn, id?: string) =>
    request<TestResult>(id ? `/connections/${id}/test` : "/connections/test", json("POST", body)),

  status: () => request<SystemStatus>("/system/status"),
  health: () => request<{ ok: boolean; version: string }>("/health"),

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
