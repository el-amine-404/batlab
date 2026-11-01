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

/** Seconds since the epoch (the account pages) or an ISO time. */
export function timeAgo(when: number | string, now = Date.now() / 1000): string {
  const seconds = typeof when === "string" ? new Date(when).getTime() / 1000 : when;
  const delta = Math.max(0, Math.round(now - seconds));
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.max(1, Math.round(delta / 60))} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  if (delta < 86400 * 30) return `${Math.round(delta / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** 95 → "about 2 min"; what is left of a job, never falsely exact. */
export function eta(seconds: number): string {
  if (seconds < 8) return "a few seconds";
  if (seconds < 50) return `about ${Math.ceil(seconds / 5) * 5} s`;
  if (seconds < 3600) return `about ${Math.max(1, Math.round(seconds / 60))} min`;
  return `about ${(seconds / 3600).toFixed(1)} h`;
}

export const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

/** How long until a time: "3 d". */
export function timeUntil(when: string, now = Date.now()): string {
  const minutes = Math.max(1, Math.round((new Date(when).getTime() - now) / 60000));
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}
