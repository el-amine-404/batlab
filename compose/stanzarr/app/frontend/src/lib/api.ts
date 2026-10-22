export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** JSON in, JSON out, same-origin cookie. A 401 sends the app back to the sign-in page. */
export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(path, {
    method: init.method ?? "GET",
    credentials: "same-origin",
    headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  })
  if (response.status === 401 && !path.startsWith("/api/login") && !path.startsWith("/api/session")) {
    window.dispatchEvent(new Event("stanzarr:signed-out"))
  }
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(response.status, detail)
  }
  return (response.status === 204 ? null : await response.json()) as T
}

export type Status = "synced" | "ai_corrected" | "ai_checked" | "ai_unchecked" | "plain" | "instrumental" | "missing"

export type SongRow = {
  id: number; artist: string; album: string; title: string; duration: number; rel: string; root: string
  status: Status; formats: string[]; languages: { code: string; name: string }[]; to_check: number | null; job: string | null
}

export type LyricsFile = {
  id: number; path: string; name: string; format: string; synced: number; language: string; language_name: string
  source: "lrclib" | "ai" | "person" | "unknown"; ai_state: "unchecked" | "checked" | "corrected" | null
  lines: number; unsure: number; checked: number; mtime: number
}

export type Job = {
  id: number; song_id: number; kind: string; status: "queued" | "running" | "done" | "failed" | "cancelled"
  stage: string; worker: string; error: string; created_at: number; started_at: number | null
  finished_at: number | null; artist?: string; title?: string
}

export type Worker = { name: string; gpu: string; vram: number; version: string; last_seen: number; online: boolean }

export type EventRow = { id: number; at: number; kind: string; song_id: number | null; message: string }

export type AppStatus = {
  version: string; user: string; read_only: boolean; background: string
  last_scan: { added?: number; updated?: number; removed?: number; seconds?: number; at?: number }
  workers_online: number; gpu_online: boolean; lrclib: boolean
}
