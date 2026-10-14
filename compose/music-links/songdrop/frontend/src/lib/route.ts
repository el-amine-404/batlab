import { useEffect, useState } from "react";

export type Route = "downloads" | "settings";

function current(): Route {
  return window.location.hash.replace(/^#\/?/, "") === "settings" ? "settings" : "downloads";
}

/** The screen in the address (#/settings), so reload and back keep it. */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(current);
  useEffect(() => {
    const onChange = () => setRoute(current());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const hrefFor = (route: Route) => (route === "downloads" ? "#/" : `#/${route}`);
