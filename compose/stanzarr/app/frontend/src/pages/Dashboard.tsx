import { Link } from "react-router-dom"
import { ArrowRight, Cpu, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { FormatBadge } from "@/components/Badges"
import { api, type EventRow, type Status } from "@/lib/api"
import { ago, STATUS, STATUS_ORDER } from "@/lib/format"
import { usePoll, useStatus } from "@/lib/status"
import { cn } from "@/lib/utils"

type Stats = {
  songs: number; by_status: Record<Status, number>
  by_language: { code: string; name: string; songs: number }[]
  by_format: { format: string; synced: number; n: number }[]
  ai_to_check: { lines: number; songs: number }; events: EventRow[]
}

function Stat({ label, value, hint, to, tone }: { label: string; value: number; hint: string; to: string; tone?: string }) {
  return (
    <Link to={to} className="group">
      <Card className="h-full transition-colors group-hover:border-foreground/20">
        <CardHeader className="pb-2">
          <CardDescription>{label}</CardDescription>
          <CardTitle className={cn("text-3xl font-semibold tabular-nums", tone)}>{value.toLocaleString()}</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">{hint}</CardContent>
      </Card>
    </Link>
  )
}

export default function Dashboard() {
  const { data } = usePoll<Stats>("/api/stats", 10000)
  const { status } = useStatus()
  if (!data) return <div className="grid gap-4 md:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32" />)}</div>

  const s = data.by_status
  const synced = s.synced + s.ai_corrected + s.ai_checked + s.ai_unchecked
  const coverage = data.songs ? Math.round((synced / data.songs) * 100) : 0

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Songs" value={data.songs} hint={`${coverage}% have synced lyrics`} to="/library" />
        <Stat label="Synced" value={synced} hint="Scroll in your players" to="/library?status=synced" tone="text-emerald-400" />
        <Stat label="AI, to check" value={s.ai_unchecked} hint={`${data.ai_to_check.lines} unsure lines to look at`}
          to="/library?status=ai_unchecked" tone="text-amber-400" />
        <Stat label="Without synced lyrics" value={s.plain + s.missing} hint={`${s.plain} plain only · ${s.missing} missing`}
          to="/library?status=missing" tone="text-rose-400" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Coverage</CardTitle>
          <CardDescription>Every song, by the best lyrics it has.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex h-3 overflow-hidden rounded-full bg-muted">
            {STATUS_ORDER.map((k) => s[k] ? (
              <div key={k} className={cn("h-full", STATUS[k].dot)} style={{ width: `${(s[k] / data.songs) * 100}%` }} title={`${STATUS[k].label}: ${s[k]}`} />
            ) : null)}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {STATUS_ORDER.map((k) => (
              <Link key={k} to={`/library?status=${k}`} className="flex items-center gap-2 text-muted-foreground hover:text-foreground">
                <span className={cn("size-2 rounded-full", STATUS[k].dot)} />{STATUS[k].label}
                <span className="font-medium text-foreground tabular-nums">{s[k]}</span>
              </Link>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle className="text-base">Languages</CardTitle><CardDescription>Songs per lyrics language</CardDescription></CardHeader>
          <CardContent className="space-y-2.5">
            {data.by_language.length === 0 && <p className="text-sm text-muted-foreground">No lyrics yet.</p>}
            {data.by_language.slice(0, 8).map((l) => {
              const top = data.by_language[0].songs || 1
              return (
                <Link key={l.code || "unknown"} to={`/library?language_code=${l.code}`} className="block space-y-1 text-sm">
                  <div className="flex justify-between"><span>{l.name}</span><span className="text-muted-foreground tabular-nums">{l.songs}</span></div>
                  <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-sky-500/70" style={{ width: `${(l.songs / top) * 100}%` }} /></div>
                </Link>
              )
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Files</CardTitle><CardDescription>Lyrics files, by kind</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            {data.by_format.map((f) => (
              <Link key={`${f.format}${f.synced}`} to={`/library?format=${f.format}`} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2"><FormatBadge format={f.format} synced={!!f.synced} />
                  <span className="text-muted-foreground">{f.synced ? "synced" : "plain"}</span></span>
                <span className="tabular-nums">{f.n}</span>
              </Link>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">AI sync</CardTitle><CardDescription>Timings from plain lyrics</CardDescription>
            <CardAction><Cpu className="size-4 text-muted-foreground" /></CardAction>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center gap-2">
              <span className={cn("size-2 rounded-full", status?.gpu_online ? "bg-emerald-500" : "bg-zinc-600")} />
              {status?.gpu_online ? "A GPU worker is online" : "No GPU worker online"}
            </div>
            <p className="text-muted-foreground">
              {status?.gpu_online ? "Queue songs from the library: plain lyrics become synced ones." :
                "AI sync runs on a computer with a graphics card. Start a worker there to enable it."}
            </p>
            <Button variant="outline" size="sm" asChild><Link to="/workers">Workers <ArrowRight /></Link></Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent activity</CardTitle>
          <CardDescription>{status?.last_scan?.at ? `Last scan ${ago(status.last_scan.at)}` : "Not scanned yet"}</CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" onClick={() => api("/api/library/scan", { method: "POST" }).then(() => toast("Scanning the library"))}>
              <RefreshCw />Scan now
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {data.events.length === 0 && <p className="text-sm text-muted-foreground">Nothing yet.</p>}
          <ul className="divide-y">
            {data.events.map((e) => (
              <li key={e.id} className="flex items-baseline gap-3 py-2 text-sm">
                <span className="w-20 shrink-0 text-xs text-muted-foreground">{ago(e.at)}</span>
                <span className="min-w-0 flex-1 truncate">{e.message}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
