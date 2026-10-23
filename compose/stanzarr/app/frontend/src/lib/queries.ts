/**
 * Every read of the API, through TanStack Query: answers are kept and shared
 * between screens; the queue and the status refresh themselves while shown.
 */
import { keepPreviousData, QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";

import { type ActivityFilters, api, type SongFilters } from "./api";

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
  songs: (filters: SongFilters) => ["songs", filters] as const,
  song: (id: number) => ["song", id] as const,
  editor: (id: number) => ["editor", id] as const,
  jobs: ["jobs"] as const,
  workers: ["workers"] as const,
  sources: ["sources"] as const,
  sourceTypes: ["sources", "types"] as const,
  notifications: ["notifications"] as const,
  notificationTypes: ["notifications", "types"] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
  stats: ["stats"] as const,
  events: ["events"] as const,
  facets: ["songs", "facets"] as const,
  history: ["songs", "history"] as const,
  activity: (filters: ActivityFilters, page: number) => ["activity", filters, page] as const,
  activityNow: ["activity", "now"] as const,
};

/** The library, one page of it; the one shown stays while the next loads. */
export const useSongs = (filters: SongFilters, page: number, size: number) =>
  useQuery({
    queryKey: [...keys.songs(filters), page, size],
    queryFn: ({ signal }) => api.songs(filters, page, size, signal),
    placeholderData: keepPreviousData,
  });
export const useFacets = () => useQuery({ queryKey: keys.facets, queryFn: api.facets, staleTime: 5 * 60 * SECOND });
export const useHistory = () => useQuery({ queryKey: keys.history, queryFn: () => api.history(90), staleTime: 5 * 60 * SECOND });
export const useActivity = (filters: ActivityFilters, page: number) =>
  useQuery({ queryKey: keys.activity(filters, page), queryFn: () => api.activity(filters, page), placeholderData: keepPreviousData,
             refetchInterval: 15 * SECOND });
/** What runs now: every 3 s while something does, else every 15 s. */
export const useActivityNow = () =>
  useQuery({ queryKey: keys.activityNow, queryFn: api.activityNow,
             refetchInterval: (q) => (q.state.data?.busy || q.state.data?.jobs.some((j) => j.status === "running") ? 3 * SECOND : 15 * SECOND) });

export const useSong = (id: number) => useQuery({ queryKey: keys.song(id), queryFn: () => api.song(id) });
export const useEditor = (id: number) =>
  useQuery({ queryKey: keys.editor(id), queryFn: () => api.editor(id), staleTime: Infinity, refetchOnWindowFocus: false });
/** The queue, every 5 s while a job runs, else every 30 s. */
export const useJobs = () =>
  useQuery({
    queryKey: keys.jobs,
    queryFn: api.jobs,
    refetchInterval: (query) => (query.state.data?.some((job) => job.status === "running" || job.status === "queued") ? 5 * SECOND : 30 * SECOND),
  });
export const useWorkers = () => useQuery({ queryKey: keys.workers, queryFn: api.workers, refetchInterval: 20 * SECOND });
export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources });
export const useSourceTypes = () => useQuery({ queryKey: keys.sourceTypes, queryFn: api.sourceTypes, staleTime: Infinity });
export const useNotifications = () => useQuery({ queryKey: keys.notifications, queryFn: api.notifications });
export const useNotificationTypes = () =>
  useQuery({ queryKey: keys.notificationTypes, queryFn: api.notificationTypes, staleTime: Infinity });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });
export const useStatus = () => useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 30 * SECOND });
export const useStats = () => useQuery({ queryKey: keys.stats, queryFn: api.stats });
export const useEvents = () => useQuery({ queryKey: keys.events, queryFn: () => api.events(50), refetchInterval: 30 * SECOND });

/** After a song's lyrics changed: it, the lists and the counts read again. */
export function useLyricsChanged() {
  const client = useQueryClient();
  return (id?: number) => {
    if (id !== undefined) {
      void client.invalidateQueries({ queryKey: keys.song(id) });
      void client.invalidateQueries({ queryKey: keys.editor(id) });
    }
    void client.invalidateQueries({ queryKey: ["songs"] });
    void client.invalidateQueries({ queryKey: keys.status });
    void client.invalidateQueries({ queryKey: keys.jobs });
    void client.invalidateQueries({ queryKey: ["activity"] });
  };
}
