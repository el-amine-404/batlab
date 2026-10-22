import { useEffect, useState } from "react";

/** value, once it has stopped changing for ms. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(handle);
  }, [value, ms]);
  return settled;
}
