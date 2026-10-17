import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

export function timeAgo(seconds: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, Math.round(now - seconds));
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  return `${Math.round(delta / 86400)} d ago`;
}

export function timeLeft(seconds: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, seconds - now);
  if (delta < 3600) return `${Math.max(1, Math.round(delta / 60))} min`;
  return `${Math.round(delta / 3600)} h`;
}

/** 90 → "1.5 h"; for delays in minutes. */
export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${+(minutes / 60).toFixed(1)} h`;
  return `${+(minutes / 1440).toFixed(1)} d`;
}

/** 231 → "3:51"; 3725 → "1:02:05". */
export function clock(seconds?: number | null): string {
  if (!seconds && seconds !== 0) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** "2026-10-16" → "16 Oct 2026". */
export function day(date?: string | null): string {
  if (!date) return "";
  const parsed = new Date(`${date}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** Whole days from today to a "YYYY-MM-DD" day, in the viewer's time zone: -1 yesterday, 6 in six days. */
export function daysFrom(date: string, now = new Date()): number | null {
  const [y, m, d] = date.split("-").map(Number);
  if (!y || !m || !d) return null;
  const then = Date.UTC(y, m - 1, d);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((then - today) / 86_400_000);
}

/** "2026-10-16" → "yesterday", "3 days ago", "2 months ago", "1 year ago"; "in 6 days" when it's to come. */
export function ago(date?: string | null, now = new Date()): string {
  const days = date ? daysFrom(date, now) : null;
  if (days === null) return "";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  const n = Math.abs(days);
  const [count, unit] = n < 31 ? [n, "day"] : n < 365 ? [Math.floor(n / 30.44), "month"] : [Math.floor(n / 365.25), "year"];
  const text = `${count} ${unit}${count === 1 ? "" : "s"}`;
  return days > 0 ? `in ${text}` : `${text} ago`;
}

/** Comes out after today. */
export function upcoming(date?: string | null): boolean {
  return !!date && (daysFrom(date) ?? 0) > 0;
}

/** "16 Oct 2026 · yesterday". */
export function dated(date?: string | null): string {
  return date ? `${day(date)} · ${ago(date)}` : "";
}

/** The YouTube picture of a video at a size: mq 320×180, hq 480×360, sd 640×480, maxres 1280×720. */
export function ytImage(id: string, size: "mq" | "hq" | "sd" | "maxres" = "mq"): string {
  return `https://i.ytimg.com/vi/${id}/${size === "maxres" ? "maxresdefault" : `${size}default`}.jpg`;
}
