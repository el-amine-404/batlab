/**
 * Every read of the API, through TanStack Query: answers are kept and shared
 * between screens. The downloads stay current through the live stream
 * (useLiveJobs): a job that changes is replaced where it is shown; a new
 * one makes the lists read again.
 */
import { type InfiniteData, keepPreviousData, QueryClient, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Job, type JobFilters, type JobPage, PAGE } from "./api";

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
  jobs: (filters: JobFilters) => ["jobs", filters] as const,
  job: (id: string) => ["job", id] as const,
  counts: ["jobs-counts"] as const,
  destinations: ["destinations"] as const,
  templateFields: ["destinations", "template"] as const,
  notifications: ["notifications"] as const,
  notificationTypes: ["notifications", "types"] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
  cookies: ["cookies"] as const,
  issues: ["issues"] as const,
};

/** Downloads, newest first, a page at a time. */
export function useJobs(filters: JobFilters) {
  return useInfiniteQuery({
    queryKey: keys.jobs(filters),
    queryFn: ({ pageParam, signal }) => api.jobs(filters, pageParam, signal),
    placeholderData: keepPreviousData,
    initialPageParam: 0,
    getNextPageParam: (last: JobPage, pages: JobPage[]) => {
      const loaded = pages.length * PAGE;
      return last.jobs.length && loaded < last.total ? loaded : undefined;
    },
  });
}

export const useJob = (id: string) => useQuery({ queryKey: keys.job(id), queryFn: () => api.job(id) });
export const useCounts = () => useQuery({ queryKey: keys.counts, queryFn: api.jobCounts });
export const useDestinations = () => useQuery({ queryKey: keys.destinations, queryFn: api.destinations });
export const useTemplateFields = () => useQuery({ queryKey: keys.templateFields, queryFn: api.templateFields, staleTime: Infinity });
export const useNotifications = () => useQuery({ queryKey: keys.notifications, queryFn: api.notifications });
export const useNotificationTypes = () =>
  useQuery({ queryKey: keys.notificationTypes, queryFn: api.notificationTypes, staleTime: Infinity });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });
export const useStatus = () => useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 30 * SECOND });
/** What needs a person's eye; read again every minute and when a download ends. */
export const useIssues = () => useQuery({ queryKey: keys.issues, queryFn: api.issues, refetchInterval: 60 * SECOND });
export const useCookies = () => useQuery({ queryKey: keys.cookies, queryFn: api.cookies });

/** After a job was asked for, retried or removed here: the lists and counts read again. */
export function useJobsChanged() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ["jobs"] });
    void client.invalidateQueries({ queryKey: keys.counts });
  };
}

/** Puts the job where it is shown; false when no list shows it yet (a new job). */
function replaceJob(client: QueryClient, job: Job): boolean {
  client.setQueryData(keys.job(job.id), job);
  let shown = false;
  client.setQueriesData<InfiniteData<JobPage>>({ queryKey: ["jobs"] }, (data) => {
    if (!data) return data;
    let changed = false;
    const pages = data.pages.map((page) => {
      if (!page.jobs.some((item) => item.id === job.id)) return page;
      changed = true;
      return { ...page, jobs: page.jobs.map((item) => (item.id === job.id ? job : item)) };
    });
    shown ||= changed;
    return changed ? { ...data, pages } : data;
  });
  return shown;
}

export type Live = "connecting" | "live" | "offline";

/**
 * The live stream of every download (GET /api/v1/jobs/events), open while the
 * page is: what it says goes straight into the cache. After a reconnect the
 * lists read again, so nothing missed while offline stays missed.
 */
export function useLiveJobs(): Live {
  const client = useQueryClient();
  const [state, setState] = useState<Live>("connecting");

  useEffect(() => {
    let refresh: number | undefined;
    const refreshLists = () => {
      window.clearTimeout(refresh);
      // Several new jobs in a burst: one read.
      refresh = window.setTimeout(() => {
        void client.invalidateQueries({ queryKey: ["jobs"] });
        void client.invalidateQueries({ queryKey: keys.counts });
        void client.invalidateQueries({ queryKey: keys.issues });
      }, 300);
    };
    const source = new EventSource("/api/v1/jobs/events");
    let opened = false;
    source.onopen = () => {
      setState("live");
      if (opened) refreshLists();
      opened = true;
    };
    source.onerror = () => setState(source.readyState === EventSource.CLOSED ? "offline" : "connecting");
    source.addEventListener("job", (event) => {
      const job = JSON.parse((event as MessageEvent<string>).data) as Job;
      const previous = client.getQueryData<Job>(keys.job(job.id));
      if (!replaceJob(client, job) || previous?.status !== job.status) refreshLists();
    });
    source.addEventListener("removed", (event) => {
      const { id } = JSON.parse((event as MessageEvent<string>).data) as { id: string };
      client.removeQueries({ queryKey: keys.job(id) });
      refreshLists();
    });
    return () => {
      window.clearTimeout(refresh);
      source.close();
    };
  }, [client]);

  return state;
}
