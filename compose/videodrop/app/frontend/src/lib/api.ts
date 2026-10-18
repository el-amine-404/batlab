/**
 * The videodrop API (/api/v1). The types come from its OpenAPI description
 * (schema.d.ts, made by `npm run gen:api`): change the backend's
 * api/schemas.py, regenerate, and TypeScript shows what to update here.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Job = Schemas["Job"];
export type JobIn = Schemas["JobIn"];
export type JobPage = Schemas["JobPage"];
export type JobCounts = Schemas["JobCounts"];
export type JobFile = Schemas["JobFile"];
export type Status = Job["status"];
export type VideoOptions = Schemas["VideoOptions"];
export type OptionsPatch = Schemas["OptionsPatch"];
export type Destination = Schemas["Destination"];
export type DestinationIn = Schemas["DestinationIn"];
export type DestinationPatch = Schemas["DestinationPatch"];
export type TemplateFields = Schemas["TemplateFields"];
export type FolderListing = Schemas["FolderListing"];
export type FolderCheck = Schemas["FolderCheck"];
export type CookieStatus = Schemas["CookieStatus"];
export type Notification = Schemas["Notification"];
export type NotificationIn = Schemas["NotificationIn"];
export type NotificationTypes = Schemas["NotificationTypes"];
export type NotificationField = Schemas["NotificationField"];
export type TestResult = Schemas["TestResult"];
export type Settings = Schemas["Settings"];
export type SettingsPatch = Schemas["SettingsPatch"];
export type SystemStatus = Schemas["SystemStatus"];
export type LinkCheck = Schemas["LinkCheck"];
export type Probe = Schemas["Probe"];
export type ProbeIn = Schemas["ProbeIn"];
export type Issue = Schemas["IssueOut"];
export type FileDetails = Schemas["FileDetails"];
export type Section = Schemas["Section"];
export type IssueList = Schemas["IssueList"];
export type JobLogLine = Schemas["JobLogLine"];
export type ServerLogLine = Schemas["ServerLogLine"];
export type LogLevel = JobLogLine["level"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}) },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Can't reach videodrop. Check that you're on the home network.", 0);
  }
  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON: a proxy's error page
    }
    throw new ApiError(detail ?? `The server answered ${response.status}.`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

/** What the list of downloads is narrowed to. */
export type JobFilters = {
  status?: Status[];
  destination?: string;
  client?: string;
  q?: string;
};

/** Downloads a page of the list. */
export const PAGE = 30;

function jobQuery(filters: JobFilters, offset: number): string {
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  for (const status of filters.status ?? []) params.append("status", status);
  if (filters.destination) params.set("destination", filters.destination);
  if (filters.client) params.set("client", filters.client);
  if (filters.q) params.set("q", filters.q);
  return params.toString();
}

export const api = {
  jobs: (filters: JobFilters, offset: number, signal?: AbortSignal) => request<JobPage>(`/jobs?${jobQuery(filters, offset)}`, { signal }),
  job: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  jobCounts: () => request<JobCounts>("/jobs/counts"),
  createJob: (body: JobIn) => request<Job>("/jobs", json("POST", body)),
  cancelJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  deleteJob: (id: string) => request<void>(`/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  jobLog: (id: string, after: number, signal?: AbortSignal) =>
    request<Schemas["JobLog"]>(`/jobs/${encodeURIComponent(id)}/log?after=${after}`, { signal }),
  serverLog: (after: number, signal?: AbortSignal) => request<Schemas["ServerLog"]>(`/log?after=${after}&level=debug`, { signal }),
  fileDetails: (id: string, index: number) => request<FileDetails>(`/jobs/${encodeURIComponent(id)}/files/${index}/details`),

  checkLink: (url: string, signal?: AbortSignal) => request<LinkCheck>(`/links/check?url=${encodeURIComponent(url)}`, { signal }),
  probe: (body: ProbeIn, signal?: AbortSignal) => request<Probe>("/probe", { ...json("POST", body), signal }),
  issues: () => request<IssueList>("/issues"),

  destinations: () => request<{ destinations: Destination[] }>("/destinations"),
  templateFields: () => request<TemplateFields>("/destinations/template"),
  createDestination: (body: DestinationIn) => request<Destination>("/destinations", json("POST", body)),
  updateDestination: (slug: string, body: DestinationPatch) =>
    request<Destination>(`/destinations/${encodeURIComponent(slug)}`, json("PATCH", body)),
  deleteDestination: (slug: string) => request<void>(`/destinations/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  folders: (path: string, signal?: AbortSignal) => request<FolderListing>(`/folders?path=${encodeURIComponent(path)}`, { signal }),
  checkFolder: (path: string, signal?: AbortSignal) => request<FolderCheck>(`/folders/check?path=${encodeURIComponent(path)}`, { signal }),

  notificationTypes: () => request<NotificationTypes>("/notifications/types"),
  notifications: () => request<{ items: Notification[] }>("/notifications"),
  createNotification: (body: NotificationIn) => request<Notification>("/notifications", json("POST", body)),
  updateNotification: (id: string, body: NotificationIn) => request<Notification>(`/notifications/${id}`, json("PUT", body)),
  deleteNotification: (id: string) => request<void>(`/notifications/${id}`, { method: "DELETE" }),
  /** Without an id: one not saved yet. With one: its saved secrets fill the gaps. */
  testNotification: (body: NotificationIn, id?: string) =>
    request<TestResult>(id ? `/notifications/${id}/test` : "/notifications/test", json("POST", body)),

  cookies: () => request<CookieStatus>("/cookies"),
  saveCookies: (file: File) => request<CookieStatus>("/cookies", { method: "PUT", body: file }),
  removeCookies: () => request<void>("/cookies", { method: "DELETE" }),

  settings: () => request<Settings>("/settings"),
  saveSettings: (patch: SettingsPatch) => request<Settings>("/settings", json("PATCH", patch)),
  status: () => request<SystemStatus>("/status"),
};

/** A file's address on this page (download_url is made for whoever asked, which may be another app). */
export function fileUrl(job: Job, file: JobFile, inline = false): string {
  return `/api/v1/jobs/${encodeURIComponent(job.id)}/files/${file.index}${inline ? "?inline=true" : ""}`;
}

/** An address the API made (with the address it was reached at) as one on this page. */
export function local(url: string): string {
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.pathname + parsed.search;
  } catch {
    return url;
  }
}

/** The message of anything thrown by api.*, for a toast. */
export function errorText(caught: unknown, fallback = "Something went wrong."): string {
  return caught instanceof ApiError ? caught.message : fallback;
}
