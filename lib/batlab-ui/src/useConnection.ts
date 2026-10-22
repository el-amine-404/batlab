import { useEffect, useState } from "react";

import type { Live } from "./Sidebar";

/**
 * Whether the page reaches its server: GET /api/v1/health (open to everyone in
 * every app) now, every 30 s while the page shows, and when the browser comes
 * back online. For the sidebar's dot in an app without a live stream of its own.
 */
export function useConnection(url = "/api/v1/health"): Live {
  const [state, setState] = useState<Live>("connecting");

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (!stopped) setState(response.ok ? "live" : "offline");
      } catch {
        if (!stopped) setState("offline");
      }
    };
    const offline = () => setState("offline");
    void check();
    const timer = window.setInterval(() => void check(), 30_000);
    window.addEventListener("online", check);
    window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", check);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("online", check);
      window.removeEventListener("offline", offline);
      document.removeEventListener("visibilitychange", check);
    };
  }, [url]);

  return state;
}
