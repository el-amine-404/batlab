import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import { usePoll } from "@/lib/status"

type SettingsData = { library: string[]; read_only: boolean; retry_days: number; scan_interval: number; lrclib_allowed: boolean; lrclib: boolean; version: string }

function Row({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 py-3">
      <div className="space-y-1"><div className="text-sm font-medium">{label}</div><div className="text-xs text-muted-foreground">{hint}</div></div>
      <div className="shrink-0 text-sm">{children}</div>
    </div>
  )
}

export default function Settings() {
  const { data, reload } = usePoll<SettingsData>("/api/settings")
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  if (!data) return null

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">Lyrics</CardTitle><CardDescription>How Stanzarr finds lyrics. Paths and intervals are set in the container's environment.</CardDescription></CardHeader>
        <CardContent className="divide-y">
          <Row label="Search LRCLIB" hint={data.lrclib_allowed ? `New songs at once; songs without lyrics synced by a person again every ${data.retry_days} days.` : "Turned off by STANZARR_LRCLIB."}>
            <Switch checked={data.lrclib} disabled={!data.lrclib_allowed}
              onCheckedChange={(v) => api("/api/settings", { method: "PUT", body: { lrclib: v } }).then(reload)} />
          </Row>
          <Row label="Library" hint="Folders scanned for songs (STANZARR_LIBRARY).">
            <div className="text-right font-mono text-xs">{data.library.map((p) => <div key={p}>{p}</div>)}</div>
          </Row>
          <Row label="Scan" hint="How often the library is read again for new or changed songs.">every {Math.round(data.scan_interval / 60)} min</Row>
          <Row label="Writing" hint="Read-only mode never writes lyrics into the library (STANZARR_READ_ONLY).">{data.read_only ? "read-only" : "writes beside songs"}</Row>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Password</CardTitle></CardHeader>
        <CardContent>
          <form className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => {
            e.preventDefault()
            api("/api/password", { method: "POST", body: { current, new: next } })
              .then(() => { toast.success("Password changed"); setCurrent(""); setNext("") })
              .catch((err) => toast.error(err.message))
          }}>
            <div className="space-y-2"><Label htmlFor="current">Current</Label>
              <Input id="current" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></div>
            <div className="space-y-2"><Label htmlFor="new">New</Label>
              <Input id="new" type="password" autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} /></div>
            <Button type="submit" disabled={!current || next.length < 8}>Change</Button>
          </form>
        </CardContent>
      </Card>

      <p className="text-center text-xs text-muted-foreground">
        Stanzarr v{data.version} · GPL-3.0 · <a className="underline underline-offset-2" href="https://github.com/el-amine-404/stanzarr" target="_blank" rel="noreferrer">github.com/el-amine-404/stanzarr</a>
      </p>
    </div>
  )
}
