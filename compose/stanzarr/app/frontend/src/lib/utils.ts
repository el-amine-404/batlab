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
  if (delta < 86400 * 60) return `${Math.round(delta / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** "in 12 min", "in 3 h", "now" when it is due. */
export function timeLeft(seconds: number, now = Date.now() / 1000): string {
  const delta = seconds - now;
  if (delta < 60) return "now";
  if (delta < 3600) return `in ${Math.round(delta / 60)} min`;
  return `in ${+(delta / 3600).toFixed(1)} h`;
}

/** 90 → "1.5 h"; for delays in minutes. */
export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${+(minutes / 60).toFixed(1)} h`;
  return `${+(minutes / 1440).toFixed(1)} d`;
}

/** The YouTube picture of a video, 320×180. */
export function ytImage(id: string): string {
  return `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
}
