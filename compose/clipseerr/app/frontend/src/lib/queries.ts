/**
 * Every read of the API, through TanStack Query: answers are kept and shared
 * between screens, a screen opened twice shows at once, and a video is fetched
 * while the pointer is still on its card (prefetchVideo).
 */
import { keepPreviousData, QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ACTIVE, api, errorText, type Family, type Kind, type Request } from "./api";

const MINUTE = 60_000;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The catalog changes slowly, and the server keeps it fresh itself.
      staleTime: 5 * MINUTE,
      gcTime: 30 * MINUTE,
      retry: (count, error) => count < 2 && !(error instanceof Error && "status" in error && (error as { status: number }).status === 404),
      refetchOnWindowFocus: false,
    },
  },
});

export const keys = {
  home: ["home"] as const,
  shelf: (id: string) => ["shelf", id] as const,
  categories: ["categories"] as const,
  category: (id: string) => ["category", id] as const,
  search: (q: string) => ["search", q] as const,
  artist: (id: string) => ["artist", id] as const,
  video: (id: string) => ["video", id] as const,
  videoRequests: (id: string) => ["video-requests", id] as const,
  requests: (status: string, q: string) => ["requests", status, q] as const,
  integrations: (family: Family) => [family] as const,
  types: (family: Family) => [family, "types"] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
};

export const useHome = () => useQuery({ queryKey: keys.home, queryFn: ({ signal }) => api.home(signal) });
export const useShelf = (id: string) =>
  useQuery({ queryKey: keys.shelf(id), queryFn: ({ signal }) => api.shelf(id, signal) });
export const useCategories = () =>
  useQuery({ queryKey: keys.categories, queryFn: ({ signal }) => api.categories(signal), staleTime: 30 * MINUTE });
export const useCategory = (id: string) =>
  useQuery({ queryKey: keys.category(id), queryFn: ({ signal }) => api.category(id, signal) });
export const useSearch = (q: string) =>
  useQuery({
    queryKey: keys.search(q),
    queryFn: ({ signal }) => api.search(q, signal),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
  });
export const useArtist = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.artist(id), queryFn: ({ signal }) => api.artist(id, signal), enabled: enabled && !!id });
export const useVideo = (id: string) =>
  useQuery({ queryKey: keys.video(id), queryFn: ({ signal }) => api.video(id, signal), staleTime: 60 * MINUTE });

const anyActive = (requests: (Request | null | undefined)[]) =>
  requests.some((request) => request && ACTIVE.includes(request.status));

export const useVideoRequests = (id: string) =>
  useQuery({
    queryKey: keys.videoRequests(id),
    queryFn: ({ signal }) => api.videoRequests(id, signal),
    staleTime: 0,
    refetchInterval: (query) => (query.state.data && anyActive([query.state.data.audio, query.state.data.video]) ? 3000 : false),
  });

export const useRequests = (status: string, q: string) =>
  useQuery({
    queryKey: keys.requests(status, q),
    queryFn: ({ signal }) => api.requests({ status, q, limit: 200 }, signal),
    staleTime: 0,
    placeholderData: keepPreviousData,
    // Every few seconds while something is under way; else now and then.
    refetchInterval: (query) => (query.state.data && query.state.data.counts.active > 0 ? 3000 : 30_000),
    refetchOnWindowFocus: true,
  });

/** How many requests are under way, for the sidebar. */
export const useActiveCount = () =>
  useQuery({
    queryKey: ["requests", "count"],
    queryFn: ({ signal }) => api.requests({ status: "active", limit: 1 }, signal),
    select: (data) => data.counts.active,
    staleTime: 0,
    refetchInterval: (query) => ((query.state.data?.counts.active ?? 0) > 0 ? 4000 : 30_000),
  });

export function prefetchVideo(id: string) {
  void queryClient.prefetchQuery({ queryKey: keys.video(id), queryFn: ({ signal }) => api.video(id, signal), staleTime: 60 * MINUTE });
}

export function prefetchArtist(id: string) {
  void queryClient.prefetchQuery({ queryKey: keys.artist(id), queryFn: ({ signal }) => api.artist(id, signal) });
}

/** Asks for a song or a video, and says so. */
export function useAsk(videoId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kind: Kind) => api.ask(videoId, kind),
    onSuccess: (request) => {
      client.setQueryData(keys.videoRequests(videoId), (old: Record<string, unknown> | undefined) => ({
        audio: null,
        video: null,
        ...old,
        [request.kind]: request,
      }));
      void client.invalidateQueries({ queryKey: ["requests"] });
      toast.success(request.kind === "audio" ? "Song requested" : "Video requested", {
        description: `${request.media.artist} – ${request.media.title}`,
      });
    },
    onError: (error) => toast.error(errorText(error, "Couldn't ask for it.")),
  });
}

/** Retry, cancel, remove: then every list is read again. */
export function useRequestAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "retry" | "cancel" | "remove" }): Promise<unknown> =>
      action === "retry" ? api.retry(id) : action === "cancel" ? api.cancel(id) : api.remove(id),
    onSuccess: (_data, { action }) => {
      void client.invalidateQueries({ queryKey: ["requests"] });
      void client.invalidateQueries({ queryKey: ["video-requests"] });
      toast.success(action === "retry" ? "Asked again" : action === "cancel" ? "Cancelled" : "Removed");
    },
    onError: (error) => toast.error(errorText(error)),
  });
}

export const useIntegrations = (family: Family) =>
  useQuery({ queryKey: keys.integrations(family), queryFn: () => api.integrations(family), staleTime: 0 });
export const useTypes = (family: Family) =>
  useQuery({ queryKey: keys.types(family), queryFn: () => api.types(family), staleTime: Infinity });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings, staleTime: 0 });
export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: api.status, staleTime: 0, refetchInterval: 10_000 });
