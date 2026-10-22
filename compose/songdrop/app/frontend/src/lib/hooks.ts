import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Detection, errorText, type OptionsSpec } from "./api";

/** Loads something once (and again on reload()), with its error as a sentence. */
export function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loader = useRef(load);

  const reload = useCallback(async () => {
    try {
      setData(await loader.current());
      setError(null);
    } catch (caught) {
      setError(errorText(caught, "Couldn't load this."));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, reload, setData };
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
          setDetection({ supported: false, message: errorText(caught, "Couldn't check the link.") });
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

export function useOptionsSpec(): OptionsSpec | null {
  return useLoad(api.options).data;
}
