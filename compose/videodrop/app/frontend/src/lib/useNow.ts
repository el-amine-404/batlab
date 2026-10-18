import { useEffect, useState } from "react";

/** Now, in Unix seconds, read again every `every` ms: for what counts down. */
export function useNow(every = 30_000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), every);
    return () => window.clearInterval(timer);
  }, [every]);
  return now;
}
