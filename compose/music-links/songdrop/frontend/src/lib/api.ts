export type Engine = "spotdl" | "ytdlp";

export type Detection =
  | {
      supported: true;
      engine: Engine;
      engine_label: string;
      site: string;
      kind: string;
      url: string;
      has_playlist: boolean;
    }
  | { supported: false; message: string };

export type Choice = { value: string; label: string };

export type Field = {
  key: string;
  type: "select" | "switch" | "time";
  flag: string;
  label: string;
  help: string;
  choices?: Choice[];
  default: string | boolean;
  kinds?: string[];
  /** has_playlist: a video inside a playlist; list: a playlist, or a video inside one. */
  needs?: "has_playlist" | "list";
};

export type OptionsSpec = { engines: Record<Engine, { label: string; fields: Field[] }> };
export type OptionValues = Record<string, string | boolean>;

export type JobStatus = "queued" | "running" | "done" | "partial" | "failed" | "cancelled";

export type JobFile = {
  id: string;
  name: string;
  size: number;
  title: string | null;
  artist: string | null;
  delivered: boolean;
  url: string;
};

export type Job = {
  id: string;
  url: string;
  engine: Engine;
  site: string;
  kind: string;
  status: JobStatus;
  title: string | null;
  message: string | null;
  total: number;
  completed: number;
  progress: number | null;
  current: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  expires_at: number | null;
  files: JobFile[];
  errors: JobError[];
  notes: { name: string; message: string }[];
  archive_url: string | null;
};

/** A song that failed: "waiting" for a later try, "queued" or "retrying" for
 * one now, "gave_up" after the last, "failed" when it can't be tried alone. */
export type RetryState = "waiting" | "queued" | "retrying" | "gave_up" | "failed";

export type JobError = {
  index: number | null;
  name: string;
  message: string;
  url: string | null;
  state: RetryState;
  tries: number;
  max_tries: number;
  next_retry_at: number | null;
};

/** The page's YouTube cookies; never the cookies themselves. */
export type CookieStatus =
  | { present: false }
  | { present: true; saved_at: number; expires_at: number | null; expired: boolean };

export const RETRYING: RetryState[] = ["queued", "retrying"];

export const ACTIVE: JobStatus[] = ["queued", "running"];

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
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
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
      // not JSON: a proxy error page
    }
    throw new ApiError(detail ?? `The server answered ${response.status}.`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  options: () => request<OptionsSpec>("/api/options"),
  detect: (url: string, signal?: AbortSignal) =>
    request<Detection>("/api/detect", { method: "POST", body: JSON.stringify({ url }), signal }),
  jobs: () => request<{ jobs: Job[]; retention_hours: number }>("/api/jobs"),
  create: (url: string, options: OptionValues) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify({ url, options }) }),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  remove: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  retry: (id: string, index: number) => request<Job>(`/api/jobs/${id}/errors/${index}/retry`, { method: "POST" }),
  cookies: () => request<CookieStatus>("/api/cookies"),
  saveCookies: (file: File) => request<CookieStatus>("/api/cookies", { method: "PUT", body: file }),
  removeCookies: () => request<void>("/api/cookies", { method: "DELETE" }),
};
