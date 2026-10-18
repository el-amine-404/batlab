import { useCallback, useMemo } from "react";
import type { SetURLSearchParams } from "react-router";

import { filterQuery, type LinkKind, type PostFilters } from "./api";

const LINKS: LinkKind[] = ["video", "reddit_video", "image", "gallery", "text", "other"];

/**
 * A list's filters, kept in the page's address (?q=…&link=video&unique=true):
 * Back finds them, and a filtered list can be shared. The same parameters as
 * the API's, so the address bar and the feed addresses read alike.
 */
export function usePostFilters([params, setParams]: [URLSearchParams, SetURLSearchParams]): [PostFilters, (f: PostFilters) => void] {
  const raw = params.toString();
  const filters = useMemo<PostFilters>(() => {
    const read = new URLSearchParams(raw);
    const link = read.get("link") as LinkKind | null;
    return {
      q: read.get("q") ?? undefined,
      link: link && LINKS.includes(link) ? link : undefined,
      unique: read.get("unique") === "true",
      feed: read.get("feed") ?? undefined,
    };
  }, [raw]);
  const set = useCallback(
    (next: PostFilters) => {
      const query = filterQuery(next);
      setParams(next.feed ? `${query}${query ? "&" : ""}feed=${encodeURIComponent(next.feed)}` : query, { replace: true });
    },
    [setParams],
  );
  return [filters, set];
}
