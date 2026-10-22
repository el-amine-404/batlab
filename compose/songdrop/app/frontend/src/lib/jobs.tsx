import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ACTIVE, api, errorText, type Job, type MissingSong, RETRYING } from "./api";

/** Downloading, waiting to, or a missing song of it in a later try now. */
export const isBusy = (job: Job) => ACTIVE.includes(job.status) || job.errors.some((e) => RETRYING.includes(e.state));

type Jobs = {
  jobs: Job[] | null;
  error: string | null;
  refresh: () => void;
  /** Queue: jobs downloading or waiting to. */
  queue: Job[];
  /** History: jobs that ended. */
  history: Job[];
  /** Missing: every missing song, with its job. */
  missing: { job: Job; song: MissingSong }[];
};

const JobsContext = createContext<Jobs | null>(null);

/** One poll of the job list for every page and the sidebar's counts: every
 * 1.5 s while something runs, every 8 s otherwise, never in a hidden tab. */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const busy = useRef(false);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    window.clearTimeout(timer.current);
    let active = false;
    try {
      const data = await api.jobs();
      setJobs(data.jobs);
      setError(null);
      active = data.jobs.some(isBusy);
    } catch (caught) {
      setError(errorText(caught, "Can't load the downloads."));
    } finally {
      busy.current = false;
      if (document.visibilityState === "visible") {
        timer.current = window.setTimeout(refresh, active ? 1500 : 8000);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
      else window.clearTimeout(timer.current);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.clearTimeout(timer.current);
    };
  }, [refresh]);

  const value = useMemo<Jobs>(() => {
    const all = jobs ?? [];
    return {
      jobs,
      error,
      refresh: () => void refresh(),
      queue: all.filter((job) => ACTIVE.includes(job.status)),
      history: all.filter((job) => !ACTIVE.includes(job.status)),
      missing: all.flatMap((job) => job.errors.map((song) => ({ job, song }))),
    };
  }, [jobs, error, refresh]);

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs(): Jobs {
  const jobs = useContext(JobsContext);
  if (!jobs) throw new Error("useJobs needs a JobsProvider");
  return jobs;
}
