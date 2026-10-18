import { useCallback, useEffect, useRef, useState } from "react";

/** Lines kept in the page at most (the oldest go). */
const KEPT = 5000;

/**
 * A log read a piece at a time: what is new since the last line (its seq),
 * every `every` ms while `follow`; once more when following stops (the last
 * lines of a download that just ended). key: another log starts afresh.
 */
export function useLogLines<T extends { seq: number }>(
  key: string,
  read: (after: number, signal: AbortSignal) => Promise<{ lines: T[]; more?: boolean }>,
  follow: boolean,
  every = 2000,
) {
  const [lines, setLines] = useState<T[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const after = useRef(0);
  const reading = useRef<Promise<void> | null>(null);
  const reader = useRef(read);
  reader.current = read;

  const readNew = useCallback((signal: AbortSignal) => {
    reading.current ??= (async () => {
      try {
        for (let page = 0; page < 10; page += 1) {
          const answer = await reader.current(after.current, signal);
          if (signal.aborted) return;
          if (answer.lines.length) {
            after.current = answer.lines[answer.lines.length - 1].seq;
            setLines((old) => [...old, ...answer.lines].slice(-KEPT));
          }
          if (!answer.more) break;
        }
        setError(null);
      } catch (caught) {
        if (!signal.aborted) setError(caught);
      } finally {
        setLoading(false);
        reading.current = null;
      }
    })();
    return reading.current;
  }, []);

  useEffect(() => {
    after.current = 0;
    setLines([]);
    setLoading(true);
  }, [key]);

  useEffect(() => {
    const stop = new AbortController();
    void readNew(stop.signal);
    const timer = follow ? window.setInterval(() => void readNew(stop.signal), every) : undefined;
    return () => {
      window.clearInterval(timer);
      stop.abort();
      reading.current = null;
    };
  }, [key, follow, every, readNew]);

  return { lines, error, loading };
}
