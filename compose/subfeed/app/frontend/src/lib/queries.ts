/**
 * Every read of the API, through TanStack Query: answers are kept and shared
 * between screens, and the feeds refresh themselves while a screen shows them.
 */
import { keepPreviousData, QueryClient, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, PAGE, type PostFilters, type PostPage } from "./api";

const SECOND = 1000;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * SECOND,
      retry: (count, error) => count < 2 && !(error instanceof Error && "status" in error && (error as { status: number }).status === 404),
      refetchOnWindowFocus: true,
    },
  },
});

export const keys = {
  feeds: ["feeds"] as const,
  posts: (filters: PostFilters) => ["posts", filters] as const,
  sources: ["sources"] as const,
  notifications: ["notifications"] as const,
  notificationTypes: ["notifications", "types"] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
};

/** The feeds, read again every 15 s: a read just asked for shows when it is done. */
export const useFeeds = () => useQuery({ queryKey: keys.feeds, queryFn: api.feeds, refetchInterval: 15 * SECOND });

/** One feed, from the list (one read for every screen). */
export function useFeed(slug: string) {
  const query = useFeeds();
  return { ...query, feed: query.data?.feeds.find((feed) => feed.slug === slug) };
}

export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources, staleTime: Infinity });

/** Posts, a page at a time; the next page loads when asked for. */
export function usePosts(filters: PostFilters) {
  return useInfiniteQuery({
    queryKey: keys.posts(filters),
    queryFn: ({ pageParam, signal }) => api.posts(filters, pageParam, signal),
    placeholderData: keepPreviousData,
    initialPageParam: 0,
    getNextPageParam: (last: PostPage, pages: PostPage[]) => {
      const loaded = pages.length * PAGE;
      return last.posts.length && loaded < last.total ? loaded : undefined;
    },
  });
}

export const useNotifications = () => useQuery({ queryKey: keys.notifications, queryFn: api.notifications });
export const useNotificationTypes = () =>
  useQuery({ queryKey: keys.notificationTypes, queryFn: api.notificationTypes, staleTime: Infinity });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });
export const useStatus = () => useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 60 * SECOND });

/** After a feed changed: its list, its posts and the status read again. */
export function useFeedsChanged() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: keys.feeds });
    void client.invalidateQueries({ queryKey: ["posts"] });
    void client.invalidateQueries({ queryKey: keys.status });
  };
}
