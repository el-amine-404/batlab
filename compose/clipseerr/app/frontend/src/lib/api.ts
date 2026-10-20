/**
 * The clipseerr API (/api/v1). The types come from its OpenAPI description
 * (schema.d.ts, made by `npm run gen:api`): change the backend's
 * app/schemas.py, regenerate, and TypeScript shows what to update here.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Video = Schemas["Video"];
export type VideoDetail = Schemas["VideoDetail"];
export type Shelf = Schemas["Shelf"];
export type Checks = Schemas["Checks"];
export type Version = Schemas["Version"];
export type Versions = Schemas["Versions"];
export type Credits = Schemas["Credits"];
export type Lyrics = Schemas["Lyrics"];
export type HotArtist = Schemas["HotArtist"];
export type CategoryGroup = Schemas["CategoryGroup"];
export type ArtistPage = Schemas["ArtistPage"];
export type ArtistSummary = Schemas["ArtistSummary"];
export type ArtistRef = Schemas["ArtistRef"];
export type SearchResults = Schemas["SearchResults"];
export type Request = Schemas["Request"];
export type RequestStatus = Request["status"];
export type RequestList = Schemas["RequestList"];
export type Kind = Request["kind"];
export type Attempt = Schemas["Attempt"];
export type VideoRequests = Schemas["VideoRequests"];
export type Integration = Schemas["Integration"];
export type IntegrationIn = Schemas["IntegrationIn"];
export type IntegrationType = Schemas["IntegrationType"];
export type IntegrationTypes = Schemas["IntegrationTypes"];
export type IntegrationField = Schemas["IntegrationField"];
export type TestResult = Schemas["TestResult"];
export type Settings = Schemas["SettingsOut"];
export type SettingsPatch = Schemas["SettingsPatch"];
export type SystemStatus = Schemas["SystemStatus"];
export type Tag = Schemas["Tag"];
export type LibraryFile = Schemas["LibraryFile"];
export type Category = Schemas["Category"];
export type AlbumPage = Schemas["AlbumPage"];
export type AlbumSummary = Schemas["AlbumSummary"];
export type Me = Schemas["Me"];
export type Permission = Me["permissions"][number];
export type AuthState = Schemas["AuthState"];
export type Account = Schemas["Account"];
export type User = Schemas["User"];
export type UserIn = Schemas["UserIn"];
export type UserPatch = Schemas["UserPatch"];
export type ApiKey = Schemas["ApiKey"];
export type ApiKeyMade = Schemas["ApiKeyMade"];

/** Under way: a service has it, or one will get it. */
export const ACTIVE: RequestStatus[] = ["queued", "sent", "downloading"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Fired when the server says nobody is signed in (401): the sign-in screen comes back. */
export const SIGNED_OUT = "clipseerr:signed-out";
/** The page's own changes say so: what another site's page can't (batlab_common/web.py). */
export const PAGE_HEADERS = { "X-Clipseerr-Page": "1" };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...PAGE_HEADERS,
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach clipseerr. Check that you're on the home network.", 0);
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
/** A query string; what is unset or false is left out ("official" only when on). */
const query = (params: Record<string, string | number | boolean | undefined>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== false).map(([k, v]) => [k, String(v)])).toString();

/** Videos a page of a shelf or a genre: two or three screens of cards. */
export const PAGE = 48;
export type TagSort = "seen" | "newest" | "views" | "title";
export type Page = {
  limit?: number;
  offset?: number;
  source?: "youtube" | "apple";
  sort?: "default" | "newest" | "views" | "title";
  official?: boolean;
};

/** Services and notifications have the same routes. */
export type Family = "services" | "notifications" | "libraries";

export const api = {
  home: (signal?: AbortSignal) => request<{ shelves: Shelf[] }>("/home", { signal }),
  shelf: (id: string, page: Page = {}, signal?: AbortSignal) =>
    request<Shelf>(`/shelves/${encodeURIComponent(id)}?${query({ limit: PAGE, offset: 0, ...page })}`, { signal }),
  categories: (signal?: AbortSignal) => request<CategoryGroup[]>("/categories", { signal }),
  category: (id: string, page: Page = {}, signal?: AbortSignal) =>
    request<Shelf>(`/categories/${encodeURIComponent(id)}?${query({ limit: PAGE, offset: 0, ...page })}`, { signal }),
  search: (q: string, page: Page = {}, signal?: AbortSignal) =>
    request<SearchResults>(`/search?${query({ q, limit: PAGE, offset: 0, ...page })}`, { signal }),
  quickSearch: (q: string, signal?: AbortSignal) => request<SearchResults>(`/search/quick?${query({ q })}`, { signal }),
  album: (id: string, signal?: AbortSignal) => request<AlbumPage>(`/albums/${encodeURIComponent(id)}`, { signal }),
  artist: (id: string, signal?: AbortSignal) => request<ArtistPage>(`/artists/${encodeURIComponent(id)}`, { signal }),
  video: (id: string, signal?: AbortSignal) => request<VideoDetail>(`/videos/${encodeURIComponent(id)}`, { signal }),
  versions: (id: string, signal?: AbortSignal) => request<Versions>(`/videos/${encodeURIComponent(id)}/versions`, { signal }),
  views: (youtubeIds: string[]) =>
    request<{ views: Record<string, string>; released?: Record<string, string> }>(`/views?ids=${youtubeIds.join(",")}`),
  files: (id: string, signal?: AbortSignal) =>
    request<{ files: LibraryFile[] }>(`/videos/${encodeURIComponent(id)}/files`, { signal }),
  tags: (signal?: AbortSignal) => request<{ tags: Tag[] }>("/tags", { signal }),
  tagged: (tags: string[], sort: TagSort, page: Page = {}, signal?: AbortSignal) =>
    request<Shelf>(`/tags/videos?${new URLSearchParams([...tags.map((t) => ["tag", t]), ["sort", sort],
                                                        ["limit", String(page.limit ?? PAGE)], ["offset", String(page.offset ?? 0)]])}`,
                   { signal }),
  credits: (id: string, signal?: AbortSignal) => request<Credits>(`/videos/${encodeURIComponent(id)}/credits`, { signal }),
  lyrics: (id: string, signal?: AbortSignal) => request<Lyrics>(`/videos/${encodeURIComponent(id)}/lyrics`, { signal }),
  /** Where the lyrics download as a file. */
  lyricsFile: (id: string, format: "lrc" | "txt") => `/api/v1/videos/${encodeURIComponent(id)}/lyrics.${format}`,
  hotPicks: (signal?: AbortSignal) => request<{ ids: string[] }>("/hot/picks", { signal }),
  addHotPick: (id: string) => request<{ ids: string[] }>(`/hot/picks/${encodeURIComponent(id)}`, { method: "PUT" }),
  removeHotPick: (id: string) => request<void>(`/hot/picks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  hotArtists: (signal?: AbortSignal) => request<{ artists: HotArtist[] }>("/hot/artists", { signal }),
  videoRequests: (id: string, signal?: AbortSignal) =>
    request<VideoRequests>(`/videos/${encodeURIComponent(id)}/requests`, { signal }),

  requests: (params: { status: string; q?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    request<RequestList>(`/requests?${query({ limit: 50, offset: 0, q: "", ...params })}`, { signal }),
  request: (id: string, signal?: AbortSignal) => request<Request>(`/requests/${id}`, { signal }),
  ask: (videoId: string, kind: Kind) => request<Request>("/requests", json("POST", { video_id: videoId, kind })),
  retry: (id: string) => request<Request>(`/requests/${id}/retry`, { method: "POST" }),
  cancel: (id: string) => request<Request>(`/requests/${id}/cancel`, { method: "POST" }),
  remove: (id: string) => request<void>(`/requests/${id}`, { method: "DELETE" }),

  types: (family: Family) => request<IntegrationTypes>(`/${family}/types`),
  integrations: (family: Family) => request<{ items: Integration[] }>(`/${family}`),
  createIntegration: (family: Family, body: IntegrationIn) => request<Integration>(`/${family}`, json("POST", body)),
  updateIntegration: (family: Family, id: string, body: IntegrationIn) =>
    request<Integration>(`/${family}/${id}`, json("PUT", body)),
  removeIntegration: (family: Family, id: string) => request<void>(`/${family}/${id}`, { method: "DELETE" }),
  orderIntegrations: (family: Family, ids: string[]) =>
    request<{ items: Integration[] }>(`/${family}/order`, json("PUT", { ids })),
  /** Without an id: one not saved yet. With one: its saved secrets fill the gaps. */
  testIntegration: (family: Family, body: IntegrationIn, id?: string) =>
    request<TestResult>(id ? `/${family}/${id}/test` : `/${family}/test`, json("POST", body)),

  settings: () => request<Settings>("/settings"),
  saveSettings: (patch: SettingsPatch) => request<Settings>("/settings", json("PATCH", patch)),
  status: () => request<SystemStatus>("/system/status"),
  clearCache: () => request<{ ok: boolean }>("/system/cache/clear", { method: "POST" }),

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
