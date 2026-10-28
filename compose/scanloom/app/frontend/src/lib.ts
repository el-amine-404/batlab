export type Point = [number, number];
export type Page = {
  id: string; position: number; original_name: string; status: string; malware_status: string; version: number;
  corners: Point[] | null; rotation: number; appearance: string; corner_radius: number; confidence: number | null;
  quality: { warnings?: string[]; blur_score?: number; glare_fraction?: number; width?: number; height?: number };
  error?: string | null; preview_url?: string | null; result_url?: string | null; download_url?: string | null; jpeg_download_url?: string | null;
};
export type Export = { id: string; kind: string; status: string; filename?: string; error?: string; expires_at: string };
export type Project = {
  public_id: string; title: string; profile: string; status: string; page_count: number;
  completed_count: number; warning_count: number; error?: string | null; expires_at: string;
  pages: Page[]; exports: Export[];
};

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail ?? message; } catch { /* plain response */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
export const cx = (...values: Array<string | false | null | undefined>) => values.filter(Boolean).join(" ");
export const expires = (value: string) => new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));

export type RecentScan = { id: string; title: string; createdAt: string; expiresAt: string; pages: number };
const RECENT_KEY = "scanloom.recent.v1";
export function recentScans(): RecentScan[] { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } }
export function rememberScan(project: Project) { const item:RecentScan={id:project.public_id,title:project.title,createdAt:new Date().toISOString(),expiresAt:project.expires_at,pages:project.page_count}; localStorage.setItem(RECENT_KEY,JSON.stringify([item,...recentScans().filter((scan)=>scan.id!==item.id)].slice(0,30))); }
export function forgetScan(id:string) { localStorage.setItem(RECENT_KEY,JSON.stringify(recentScans().filter((scan)=>scan.id!==id))); }
