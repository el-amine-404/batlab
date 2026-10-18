/**
 * Every read of the API, through TanStack Query: answers are kept and shared
 * between screens, a screen opened twice shows at once, and a video is fetched
 * while the pointer is still on its card (prefetchVideo).
 */
import { keepPreviousData, QueryClient, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ACTIVE, api, errorText, type Family, type Kind, PAGE, type Page, type Request, type SearchResults, type Shelf, type TagSort } from "./api";

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
  quickSearch: (q: string) => ["search", "quick", q] as const,
  artist: (id: string) => ["artist", id] as const,
  video: (id: string) => ["video", id] as const,
  versions: (id: string) => ["versions", id] as const,
  videoRequests: (id: string) => ["video-requests", id] as const,
  requests: (status: string, q: string) => ["requests", status, q] as const,
  integrations: (family: Family) => [family] as const,
  types: (family: Family) => [family, "types"] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
};

export const useHome = () => useQuery({ queryKey: keys.home, queryFn: ({ signal }) => api.home(signal) });
/**
 * A shelf or a genre, a page at a time: the next page loads when it is
 * asked for (useInfiniteList), and pages stay cached, so going back finds the
 * list as it was left.
 */
export function useShelfPages(kind: "shelf" | "category", id: string, source?: "youtube" | "apple", sort: Page["sort"] = "default") {
  return useInfiniteQuery({
    queryKey: [kind, id, "pages", source ?? "all", sort] as const,
    queryFn: ({ pageParam, signal }) =>
      (kind === "shelf" ? api.shelf : api.category)(id, { limit: PAGE, offset: pageParam, sort, ...(source && { source }) }, signal),
    placeholderData: keepPreviousData,
    initialPageParam: 0,
    getNextPageParam: (last: Shelf, pages: Shelf[]) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return last.items.length && loaded < last.total ? loaded : undefined;
    },
  });
}
/** What came out lately and what is coming, newest first (the shelf "releases"). */
export const useReleases = (limit = 60) =>
  useQuery({ queryKey: [...keys.shelf("releases"), limit], queryFn: ({ signal }) => api.shelf("releases", { limit }, signal) });
export const useCategories = () =>
  useQuery({ queryKey: keys.categories, queryFn: ({ signal }) => api.categories(signal), staleTime: 30 * MINUTE });
/** A search, a page of videos at a time (the artists come with the first). */
export const useSearch = (q: string, sort: Page["sort"] = "default", official = false) =>
  useInfiniteQuery({
    queryKey: [...keys.search(q), sort, official] as const,
    queryFn: ({ pageParam, signal }) => api.search(q, { limit: PAGE, offset: pageParam, sort, official }, signal),
    initialPageParam: 0,
    getNextPageParam: (last: SearchResults, pages: SearchResults[]) => {
      const loaded = pages.reduce((sum, page) => sum + page.videos.length, 0);
      return last.videos.length && loaded < (last.total ?? 0) ? loaded : undefined;
    },
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
  });
/** The same search among the videos clipseerr has shown: answers at once, while YouTube Music's takes a second. */
export const useQuickSearch = (q: string) =>
  useQuery({
    queryKey: keys.quickSearch(q),
    queryFn: ({ signal }) => api.quickSearch(q, signal),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: 0,
  });
export const useAlbum = (id: string | null | undefined, enabled = true) =>
  useQuery({ queryKey: ["album", id], queryFn: ({ signal }) => api.album(id!, signal), enabled: enabled && !!id, staleTime: 60 * MINUTE });
export const useArtist = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.artist(id), queryFn: ({ signal }) => api.artist(id, signal), enabled: enabled && !!id });
export const useVideo = (id: string) =>
  useQuery({ queryKey: keys.video(id), queryFn: ({ signal }) => api.video(id, signal), staleTime: 60 * MINUTE });

export const useVersions = (id: string, enabled = true) =>
  useQuery({ queryKey: keys.versions(id), queryFn: ({ signal }) => api.versions(id, signal), enabled: enabled && !!id, staleTime: 60 * MINUTE });

export const useCredits = (id: string, enabled = true) =>
  useQuery({ queryKey: ["credits", id], queryFn: ({ signal }) => api.credits(id, signal), enabled: enabled && !!id, staleTime: 60 * MINUTE });
export const useLyrics = (id: string, enabled = true) =>
  useQuery({ queryKey: ["lyrics", id], queryFn: ({ signal }) => api.lyrics(id, signal), enabled: enabled && !!id, staleTime: 60 * MINUTE });
/*
 * Views, for the cards whose list didn't carry them: the cards on screen ask
 * within a moment of each other, and go out together, 48 a call.
 */
const VIEWS_BATCH = 48;
export type Facts = { views: string | null; released: string | null };
let waiting = new Map<string, ((facts: Facts) => void)[]>();
let flushing: number | undefined;

function flushViews() {
  const batch = waiting;
  waiting = new Map();
  flushing = undefined;
  const ids = [...batch.keys()];
  for (let i = 0; i < ids.length; i += VIEWS_BATCH) {
    const chunk = ids.slice(i, i + VIEWS_BATCH);
    const settle = (views: Record<string, string>, released: Record<string, string> = {}) =>
      chunk.forEach((id) => batch.get(id)!.forEach((done) => done({ views: views[id] ?? null, released: released[id] ?? null })));
    api.views(chunk).then((answer) => settle(answer.views, answer.released), () => settle({}));
  }
}

function factsOf(youtubeId: string): Promise<Facts> {
  return new Promise((done) => {
    waiting.set(youtubeId, [...(waiting.get(youtubeId) ?? []), done]);
    flushing ??= window.setTimeout(flushViews, 50);
  });
}

/** A video's views and the day it came out, when its list didn't say: looked up once the card is shown. */
export const useFacts = (youtubeId: string | null | undefined, enabled: boolean) =>
  useQuery({ queryKey: ["facts", youtubeId], queryFn: () => factsOf(youtubeId!), enabled: enabled && !!youtubeId,
             staleTime: 60 * MINUTE, retry: false });

/** The video's files in the libraries, to download. */
export const useFiles = (id: string, enabled = true) =>
  useQuery({ queryKey: ["files", id], queryFn: ({ signal }) => api.files(id, signal), enabled: enabled && !!id, staleTime: MINUTE });
export const useTags = () => useQuery({ queryKey: ["tags"], queryFn: ({ signal }) => api.tags(signal), staleTime: 5 * MINUTE });
/** The videos carrying every tag, a page at a time. */
export function useTagged(tags: string[], sort: TagSort) {
  return useInfiniteQuery({
    queryKey: ["tagged", [...tags].sort().join("+"), sort] as const,
    queryFn: ({ pageParam, signal }) => api.tagged(tags, sort, { limit: PAGE, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last: Shelf, pages: Shelf[]) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return last.items.length && loaded < last.total ? loaded : undefined;
    },
    enabled: tags.length > 0,
  });
}

export const useHotPicks = () => useQuery({ queryKey: ["hot", "picks"], queryFn: ({ signal }) => api.hotPicks(signal), staleTime: 0 });
export const useHotArtists = () =>
  useQuery({ queryKey: ["hot", "artists"], queryFn: ({ signal }) => api.hotArtists(signal), staleTime: 10 * MINUTE });

/** Adds a video to Hot, or takes it out; Hot is read again. */
export function useHotPick() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, add }: { id: string; add: boolean }): Promise<unknown> => (add ? api.addHotPick(id) : api.removeHotPick(id)),
    onSuccess: (_data, { add }) => {
      void client.invalidateQueries({ queryKey: ["hot"] });
      void client.invalidateQueries({ queryKey: ["category", "hot"] });
      void client.invalidateQueries({ queryKey: ["shelf", "releases"] });
      toast.success(add ? "Added to Hot" : "Taken out of Hot");
    },
    onError: (error) => toast.error(errorText(error)),
  });
}

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
