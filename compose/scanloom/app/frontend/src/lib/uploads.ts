/**
 * Photos on their way to a scan: one at a time, in order (so the pages keep the
 * order they were given), carrying on across screens, each with its progress.
 */
import { useSyncExternalStore } from "react";
import { toast } from "sonner";

import { ApiError, type Page, PAGE_HEADER, type Point } from "./api";
import { queryClient } from "./queries";

export type Upload = {
  id: string; project: string; file: File; hint?: Point[]; status: "waiting" | "sending" | "failed"; sent: number; error?: string; preview?: string;
};

export const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif,.jpg,.jpeg,.png,.webp";
const IMAGE = /^image\/(jpeg|png|webp|heic|heif)$/;
const EXTENSION = /\.(jpe?g|png|webp|heic|heif)$/i;
/** Browsers other than Safari can't show these: their tile says so instead of a blank square. */
const SHOWABLE = /^image\/(jpeg|png|webp)$/;

let uploads: Upload[] = [];
let snapshot: Upload[] = uploads;
const listeners = new Set<() => void>();
let running: XMLHttpRequest | null = null;

const changed = () => {
  snapshot = [...uploads];
  listeners.forEach((listener) => listener());
};

export const isImage = (file: File) => IMAGE.test(file.type) || EXTENSION.test(file.name);

export function useUploads(project?: string): Upload[] {
  const all = useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, () => snapshot);
  return project ? all.filter((item) => item.project === project) : all;
}

export function addUploads(project: string, files: File[], hint?: Point[]): void {
  const usable = files.filter(isImage);
  if (usable.length < files.length) toast.warning(`${files.length - usable.length} left out: only JPEG, PNG, WebP and HEIC images can be scanned.`);
  uploads.push(...usable.map((file) => ({
    id: crypto.randomUUID?.() ?? String(Math.random()),
    project, file, hint, status: "waiting" as const, sent: 0,
    preview: SHOWABLE.test(file.type) ? URL.createObjectURL(file) : undefined,
  })));
  changed();
  next();
}

function finish(item: Upload) {
  if (item.preview) window.setTimeout(() => URL.revokeObjectURL(item.preview!), 30_000);
  uploads = uploads.filter((other) => other !== item);
  running = null;
  changed();
  next();
}

export function cancelUpload(id: string): void {
  const item = uploads.find((other) => other.id === id);
  if (!item) return;
  if (item.status === "sending") running?.abort();
  else {
    uploads = uploads.filter((other) => other !== item);
    changed();
  }
}

export function retryUpload(id: string): void {
  const item = uploads.find((other) => other.id === id);
  if (!item) return;
  item.status = "waiting";
  item.error = undefined;
  changed();
  next();
}

function next(): void {
  if (running) return;
  const item = uploads.find((other) => other.status === "waiting");
  if (!item) return;
  const xhr = new XMLHttpRequest();
  running = xhr;
  item.status = "sending";
  changed();
  const query = new URLSearchParams({ name: item.file.name || "photo.jpg", hold: "true" });
  if (item.hint) query.set("hint", JSON.stringify(item.hint));
  xhr.open("POST", `/api/v2/scans/${encodeURIComponent(item.project)}/pages?${query}`);
  xhr.setRequestHeader(PAGE_HEADER, "1");
  xhr.upload.onprogress = (event) => {
    item.sent = event.lengthComputable ? event.loaded / event.total : 0;
    changed();
  };
  const failed = (message: string) => {
    item.status = "failed";
    item.error = message;
    running = null;
    changed();
    next();
  };
  xhr.onload = () => {
    if (xhr.status === 401) window.dispatchEvent(new Event("scanloom:signed-out"));
    if (xhr.status >= 300) {
      let detail = `The server answered ${xhr.status}.`;
      try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch { /* a proxy's page */ }
      return failed(detail);
    }
    const answer = JSON.parse(xhr.responseText) as { duplicate: boolean; page: Page };
    if (answer.duplicate) toast.info(`${item.file.name} is already page ${answer.page.position + 1} of this scan: not added twice.`);
    void queryClient.invalidateQueries({ queryKey: ["project", item.project] });
    void queryClient.invalidateQueries({ queryKey: ["pages", item.project] });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    finish(item);
  };
  xhr.onerror = () => failed("The connection dropped.");
  xhr.onabort = () => finish(item);
  xhr.send(item.file);
}

export { ApiError };

// Leaving with photos still on their way would lose them.
window.addEventListener("beforeunload", (event) => {
  if (uploads.some((item) => item.status !== "failed")) event.preventDefault();
});
