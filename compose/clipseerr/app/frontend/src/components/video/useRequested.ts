import { useMemo } from "react";

import type { Kind, RequestStatus } from "@/lib/api";
import { useRequests } from "@/lib/queries";

export type Requested = Partial<Record<Kind, RequestStatus>>;

/** What has been asked for, by video: from the newest 200 requests, already polled by the sidebar's counter. */
export function useRequested(): Map<string, Requested> {
  const { data } = useRequests("all", "");
  return useMemo(() => {
    const found = new Map<string, Requested>();
    for (const request of data?.requests ?? []) {
      if (request.status === "cancelled") continue;
      const entry = found.get(request.media.video_key) ?? {};
      entry[request.kind] ??= request.status;
      found.set(request.media.video_key, entry);
    }
    return found;
  }, [data]);
}
