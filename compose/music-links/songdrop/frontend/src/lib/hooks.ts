import { useCallback, useEffect, useRef, useState } from "react";

import { ACTIVE, api, ApiError, RETRYING, type Detection, type Job, type OptionsSpec } from "./api";

/** The job list, polled quickly while something is running. */
export function useJobs() {
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
      // A song being tried again counts as running: its file appears soon.
      active = data.jobs.some(
        (job) => ACTIVE.includes(job.status) || job.errors.some((error) => RETRYING.includes(error.state)),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Can't load the downloads.");
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

  return { jobs, error, refresh, setJobs };
}

/** What the server makes of a link, asked once typing pauses. */
export function useDetect(url: string) {
  const [detection, setDetection] = useState<Detection | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const trimmed = url.trim();
    if (!trimmed) {
      setDetection(null);
      setChecking(false);
      return;
    }
    const controller = new AbortController();
    setChecking(true);
    const handle = window.setTimeout(async () => {
      try {
        setDetection(await api.detect(trimmed, controller.signal));
      } catch (caught) {
        if (!controller.signal.aborted) {
          setDetection({ supported: false, message: caught instanceof Error ? caught.message : "Couldn't check the link." });
        }
      } finally {
        if (!controller.signal.aborted) setChecking(false);
      }
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(handle);
    };
  }, [url]);

  return { detection, checking };
}

export function useOptionsSpec() {
  const [spec, setSpec] = useState<OptionsSpec | null>(null);
  useEffect(() => {
    api.options().then(setSpec, () => setSpec(null));
  }, []);
  return spec;
}

/** Per-browser memory of chosen options; storage can be missing or blocked. */
export const stored = {
  get<T>(key: string): T | null {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  },
  set(key: string, value: unknown) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // private mode or blocked storage: the defaults still work
    }
  },
};
