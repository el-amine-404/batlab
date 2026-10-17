import { useEffect, useState } from "react";

/** The time in seconds, again every `every` seconds: for "updated 3 min ago" that stays true. */
export function useNow(every = 30): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), every * 1000);
    return () => window.clearInterval(timer);
  }, [every]);
  return now;
}
