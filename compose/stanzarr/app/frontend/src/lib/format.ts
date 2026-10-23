/** How the library's words read on the page: statuses, formats, where lyrics came from, times. */
import type { LyricsStatus } from "./api";

/**
 * The statuses' colours: the dots, the bars, the charts. One hue each, in the
 * ladder's order, checked for colour-blind readers (adjacent parts stay apart);
 * the words beside them stay in the text colour.
 */
export const STATUS: Record<LyricsStatus, { label: string; hint: string; color: string; dot: string }> = {
  synced: { label: "Synced", hint: "Synced lyrics made by a person or a source", color: "#199e70", dot: "bg-[#199e70]" },
  ai_corrected: { label: "AI, corrected", hint: "Synced by AI, then fixed by a person", color: "#3987e5", dot: "bg-[#3987e5]" },
  ai_checked: { label: "AI, checked", hint: "Synced by AI and approved by a person", color: "#008300", dot: "bg-[#008300]" },
  ai_unchecked: { label: "AI, to check", hint: "Synced by AI; nobody has looked yet", color: "#c98500", dot: "bg-[#c98500]" },
  plain: { label: "Plain only", hint: "Words without timings: players won't scroll them", color: "#d55181", dot: "bg-[#d55181]" },
  instrumental: { label: "Instrumental", hint: "No words to sing", color: "#9085e9", dot: "bg-[#9085e9]" },
  missing: { label: "Missing", hint: "No lyrics at all yet", color: "#e66767", dot: "bg-[#e66767]" },
};

export const STATUS_ORDER: LyricsStatus[] = ["synced", "ai_corrected", "ai_checked", "ai_unchecked", "plain", "instrumental", "missing"];

export const FORMAT: Record<string, string> = {
  lrc: "LRC", elrc: "Enhanced LRC", txt: "TXT", ttml: "TTML", yaml: "YAML", embedded: "Embedded",
};

/** Who made a lyrics file. */
export const SOURCE: Record<string, string> = {
  lrclib: "LRCLIB", netease: "NetEase", musixmatch: "Musixmatch", genius: "Genius", lyricsovh: "lyrics.ovh", ai: "AI sync", person: "A person",
  unknown: "Unknown",
};

/** What a file's timings are: words in time, lines in time, none. */
export function timings(format: string, synced: boolean): { label: string; hint: string } {
  if (format === "elrc") return { label: "Words", hint: "Every word in time (enhanced LRC): karaoke-style players" };
  if (synced) return { label: "Lines", hint: "Every line in time: players scroll it with the song" };
  return { label: "None", hint: "Words only: players show them, but don't scroll" };
}

/** The quality ladder (Settings, General), as the server ranks a song's best lyrics. */
export const LEVELS = ["Nothing", "Plain words", "AI synced", "AI, checked", "Synced by a person", "Words in time"];
export const CUTOFFS: { value: "plain" | "ai" | "ai_checked" | "synced" | "words"; label: string; rank: number }[] = [
  { value: "plain", label: "Plain words", rank: 1 },
  { value: "ai", label: "AI synced", rank: 2 },
  { value: "ai_checked", label: "AI synced, checked by a person", rank: 3 },
  { value: "synced", label: "Synced by a person", rank: 4 },
  { value: "words", label: "Words in time", rank: 5 },
];

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
