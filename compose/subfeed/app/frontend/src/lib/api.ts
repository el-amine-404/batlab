/**
 * The subfeed API (/api/v1). The types come from its OpenAPI description
 * (schema.d.ts, made by `npm run gen:api`): change the backend's
 * api/schemas.py, regenerate, and TypeScript shows what to update here.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Feed = Schemas["Feed"];
export type FeedIn = Schemas["FeedIn"];
export type FeedPatch = Schemas["FeedPatch"];
export type SourceInfo = Schemas["SourceInfo"];
export type Post = Schemas["PostOut"];
export type PostPage = Schemas["PostPage"];
export type LinkKind = Post["link"]["kind"];
export type Notification = Schemas["Notification"];
export type NotificationIn = Schemas["NotificationIn"];
export type NotificationTypes = Schemas["NotificationTypes"];
export type NotificationField = Schemas["NotificationField"];
export type TestResult = Schemas["TestResult"];
export type Settings = Schemas["Settings"];
export type SettingsPatch = Schemas["SettingsPatch"];
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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Fired when the server says nobody is signed in (401): the sign-in screen comes back. */
export const SIGNED_OUT = "subfeed:signed-out";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        // The page's own changes say so: what another site's page can't (batlab_common/web.py).
        "X-Subfeed-Page": "1",
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach subfeed. Check that you're on the home network.", 0);
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

/** What a list of posts is narrowed to; the same parameters on every read (…/posts, feed.json, feed.atom). */
export type PostFilters = {
  feed?: string;
  link?: LinkKind;
  video?: "youtube";
  q?: string;
  unique?: boolean;
};

/** The query string of filters, without what is unset (unique only when on); the feed is in the path. */
export function filterQuery({ feed: _feed, ...filters }: PostFilters, extra: Record<string, string | number> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) {
    if (value !== undefined && value !== "" && value !== false) params.set(key, String(value));
  }
  return params.toString();
}

/** Posts a page of the list. */
export const PAGE = 50;

export const api = {
  sources: () => request<{ sources: SourceInfo[] }>("/sources"),
  feeds: () => request<{ feeds: Feed[] }>("/feeds"),
  feed: (slug: string) => request<Feed>(`/feeds/${encodeURIComponent(slug)}`),
  createFeed: (body: FeedIn) => request<Feed>("/feeds", json("POST", body)),
  updateFeed: (slug: string, body: FeedPatch) => request<Feed>(`/feeds/${encodeURIComponent(slug)}`, json("PATCH", body)),
  deleteFeed: (slug: string) => request<void>(`/feeds/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  refreshFeed: (slug: string) => request<Feed>(`/feeds/${encodeURIComponent(slug)}/refresh`, { method: "POST" }),

  posts: (filters: PostFilters, offset: number, signal?: AbortSignal) => {
    const query = filterQuery(filters, { limit: PAGE, offset });
    const path = filters.feed ? `/feeds/${encodeURIComponent(filters.feed)}/posts` : "/posts";
    return request<PostPage>(`${path}?${query}`, { signal });
  },

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
  status: () => request<SystemStatus>("/status"),

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

/** The addresses other programs read a feed at, narrowed as the page is. */
export function feedAddresses(slug: string, filters: PostFilters) {
  const base = `${window.location.origin}/api/v1/feeds/${encodeURIComponent(slug)}`;
  const query = filterQuery(filters);
  const tail = query ? `?${query}` : "";
  return { json: `${base}/feed.json${tail}`, atom: `${base}/feed.atom${tail}`, api: `${base}/posts${tail}` };
}

/** The message of anything thrown by api.*, for a toast. */
export function errorText(caught: unknown, fallback = "Something went wrong."): string {
  return caught instanceof ApiError ? caught.message : fallback;
}
