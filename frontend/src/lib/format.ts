import type { Status } from "./api"

export const STATUS: Record<Status, { label: string; hint: string; dot: string; tone: string }> = {
  synced: { label: "Synced", hint: "Synced lyrics made by a person", dot: "bg-emerald-500", tone: "text-emerald-400" },
  ai_corrected: { label: "AI, corrected", hint: "Synced by AI, then fixed by a person", dot: "bg-sky-500", tone: "text-sky-400" },
  ai_checked: { label: "AI, checked", hint: "Synced by AI and approved by a person", dot: "bg-sky-400", tone: "text-sky-300" },
  ai_unchecked: { label: "AI, to check", hint: "Synced by AI; nobody has looked yet", dot: "bg-amber-500", tone: "text-amber-400" },
  plain: { label: "Plain only", hint: "Words without timings: players will not scroll them", dot: "bg-zinc-400", tone: "text-zinc-300" },
  instrumental: { label: "Instrumental", hint: "No words to sing", dot: "bg-zinc-600", tone: "text-zinc-500" },
  missing: { label: "Missing", hint: "No lyrics at all yet", dot: "bg-rose-500", tone: "text-rose-400" },
}

export const STATUS_ORDER: Status[] = ["synced", "ai_corrected", "ai_checked", "ai_unchecked", "plain", "instrumental", "missing"]

export const FORMAT: Record<string, string> = {
  lrc: "LRC", elrc: "Enhanced LRC", txt: "TXT", ttml: "TTML", yaml: "YAML", embedded: "Embedded",
}

export const SOURCE: Record<string, string> = {
  lrclib: "LRCLIB", ai: "AI sync", person: "A person", unknown: "Unknown",
}

/** 83.4567 -> "1:23.457" */
export function clock(seconds: number): string {
  const s = Math.max(0, seconds || 0)
  const m = Math.floor(s / 60)
  return `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}`
}

/** 213 -> "3:33" */
export function length(seconds: number): string {
  const s = Math.round(seconds || 0)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

export function ago(at: number | null | undefined): string {
  if (!at) return "never"
  const s = Math.max(0, Date.now() / 1000 - at)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
