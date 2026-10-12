import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { api, type AppStatus } from "./api"

const Context = createContext<{ status: AppStatus | null; refresh: () => void }>({ status: null, refresh: () => {} })

/** The app's state (workers, background work), refreshed every 5 seconds. */
export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AppStatus | null>(null)
  const refresh = () => { api<AppStatus>("/api/status").then(setStatus).catch(() => {}) }
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [])
  return <Context.Provider value={{ status, refresh }}>{children}</Context.Provider>
}

export const useStatus = () => useContext(Context)

/** Load a URL, again every `every` ms when given; `reload` forces it now. */
export function usePoll<T>(url: string | null, every = 0): { data: T | null; error: string; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState("")
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!url) return
    let live = true
    const load = () => api<T>(url).then((d) => { if (live) { setData(d); setError("") } })
      .catch((e) => { if (live) setError(e.message) })
    load()
    const timer = every ? setInterval(load, every) : 0
    return () => { live = false; if (timer) clearInterval(timer) }
  }, [url, every, tick])
  return { data, error, reload: () => setTick((t) => t + 1) }
}
