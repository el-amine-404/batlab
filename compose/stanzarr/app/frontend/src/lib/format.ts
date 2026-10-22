/** How the library's words read on the page: statuses, formats, where lyrics came from, times. */
import type { LyricsStatus } from "./api";

export const STATUS: Record<LyricsStatus, { label: string; hint: string; dot: string; tone: string; bar: string }> = {
  synced: { label: "Synced", hint: "Synced lyrics made by a person", dot: "bg-emerald-400", tone: "text-emerald-300", bar: "bg-emerald-400" },
  ai_corrected: { label: "AI, corrected", hint: "Synced by AI, then fixed by a person", dot: "bg-teal-400", tone: "text-teal-300", bar: "bg-teal-400" },
  ai_checked: { label: "AI, checked", hint: "Synced by AI and approved by a person", dot: "bg-cyan-400", tone: "text-cyan-300", bar: "bg-cyan-400" },
  ai_unchecked: { label: "AI, to check", hint: "Synced by AI; nobody has looked yet", dot: "bg-warning", tone: "text-warning", bar: "bg-warning" },
  plain: { label: "Plain only", hint: "Words without timings: players won't scroll them", dot: "bg-zinc-400", tone: "text-zinc-300", bar: "bg-zinc-500" },
  instrumental: { label: "Instrumental", hint: "No words to sing", dot: "bg-zinc-600", tone: "text-zinc-400", bar: "bg-zinc-700" },
  missing: { label: "Missing", hint: "No lyrics at all yet", dot: "bg-rose-400", tone: "text-rose-300", bar: "bg-rose-400" },
};

export const STATUS_ORDER: LyricsStatus[] = ["synced", "ai_corrected", "ai_checked", "ai_unchecked", "plain", "instrumental", "missing"];

export const FORMAT: Record<string, string> = {
  lrc: "LRC", elrc: "Enhanced LRC", txt: "TXT", ttml: "TTML", yaml: "YAML", embedded: "Embedded",
};

export const SOURCE: Record<string, string> = {
  lrclib: "LRCLIB", ai: "AI sync", person: "A person", unknown: "Unknown",
};

/** 83.4567 -> "1:23.457" */
export function clock(seconds: number): string {
  const s = Math.max(0, seconds || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}`;
}

/** 213 -> "3:33" */
export function length(seconds: number): string {
  const s = Math.round(seconds || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
