/**
 * Everything on screen stays current by itself: one stream from the server says
 * what changed, in any browser, on any device, and only those answers are read
 * again. The stream is reopened and everything re-read whenever the page comes
 * back: a phone suspends a page in the background and its stream with it. A slow
 * poll stays underneath in case a network in between swallows the stream.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

type Change = { project?: string | null; event?: string };

const LISTS = new Set(["project_created", "project_deleted", "project_renamed", "project_ready", "page_added", "pages_deleted", "failed", "resync"]);
const THROTTLE_MS = 700;
const SAFETY_POLL_MS = 45_000;

export function useLiveUpdates(): void {
  const client = useQueryClient();

  useEffect(() => {
    let stream: EventSource | null = null;
    let timer: number | undefined;
    const pending = new Set<string>();
    let lists = false;

    const flush = () => {
      timer = undefined;
      if (lists) void client.invalidateQueries({ queryKey: ["projects"] });
      for (const id of pending) {
        void client.invalidateQueries({ queryKey: ["project", id] });
        void client.invalidateQueries({ queryKey: ["pages", id] });
      }
      pending.clear();
      lists = false;
    };

    const everything = () => {
      void client.invalidateQueries({ predicate: (query) => ["projects", "project", "pages"].includes(String(query.queryKey[0])) });
    };

    const onChange = (raw: MessageEvent<string>) => {
      const change = JSON.parse(raw.data) as Change;
      if (change.event === "resync") return everything();
      if (change.project) pending.add(change.project);
      if (change.event && LISTS.has(change.event)) lists = true;
      if (timer === undefined) timer = window.setTimeout(flush, THROTTLE_MS);
    };

    const open = () => {
      stream?.close();
      stream = new EventSource("/api/v1/events");
      stream.addEventListener("change", onChange as EventListener);
      // A browser gives up on a stream that was refused: try again later, not never.
      stream.onerror = () => {
        if (stream?.readyState === EventSource.CLOSED) window.setTimeout(() => { if (!document.hidden) open(); }, 5000);
      };
    };

    const back = () => {
      if (document.hidden) return;
      if (!stream || stream.readyState !== EventSource.OPEN) open();
      everything();
    };

    open();
    const poll = window.setInterval(() => { if (!document.hidden) everything(); }, SAFETY_POLL_MS);
    document.addEventListener("visibilitychange", back);
    window.addEventListener("online", back);
    window.addEventListener("pageshow", back);
    return () => {
      stream?.close();
      window.clearInterval(poll);
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", back);
      window.removeEventListener("online", back);
      window.removeEventListener("pageshow", back);
    };
  }, [client]);
}
