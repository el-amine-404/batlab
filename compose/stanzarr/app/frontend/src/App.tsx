import { useEffect, useState } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { Skeleton } from "@/components/ui/skeleton"
import { Layout } from "@/components/Layout"
import { api } from "@/lib/api"
import { StatusProvider } from "@/lib/status"
import Dashboard from "@/pages/Dashboard"
import Editor from "@/pages/Editor"
import Library from "@/pages/Library"
import Queue from "@/pages/Queue"
import Settings from "@/pages/Settings"
import SignIn from "@/pages/SignIn"
import Workers from "@/pages/Workers"

type Session = { user: string | null; setup: boolean; version: string }

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const load = () => api<Session>("/api/session").then(setSession).catch(() => setSession({ user: null, setup: false, version: "" }))
  useEffect(() => {
    load()
    const out = () => setSession((s) => (s ? { ...s, user: null } : s))
    window.addEventListener("stanzarr:signed-out", out)
    return () => window.removeEventListener("stanzarr:signed-out", out)
  }, [])

  if (!session) return <div className="grid h-svh place-items-center"><Skeleton className="h-8 w-48" /></div>
  if (!session.user) return <SignIn setup={session.setup} onDone={load} />
  return (
    <StatusProvider>
      <Routes>
        <Route path="/editor/:id" element={<Editor />} />
        <Route element={<Layout user={session.user} version={session.version} onSignOut={load} />}>
          <Route index element={<Dashboard />} />
          <Route path="library" element={<Library />} />
          <Route path="queue" element={<Queue />} />
          <Route path="workers" element={<Workers />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </StatusProvider>
  )
}
