import { useEffect, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { ChevronLeft, ChevronRight, MoreHorizontal, PenLine, Search, Sparkles, Wand2, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { FormatBadge, LanguageBadge, StatusBadge } from "@/components/Badges"
import { SongSheet } from "@/components/SongSheet"
import { api, type SongRow, type Status } from "@/lib/api"
import { length, STATUS, STATUS_ORDER } from "@/lib/format"
import { usePoll, useStatus } from "@/lib/status"

type Page = { total: number; page: number; size: number; songs: SongRow[] }

export async function searchNow(id: number) {
  const t = toast.loading("Asking LRCLIB…")
  try {
    const r = await api<{ result: string }>(`/api/songs/${id}/search`, { method: "POST" })
    toast.success(r.result, { id: t })
  } catch (e) {
    toast.error((e as Error).message, { id: t })
  }
}

export async function aiSync(id: number) {
  try {
    await api(`/api/songs/${id}/ai-sync`, { method: "POST" })
    toast.success("Queued for AI sync")
  } catch (e) {
    toast.error((e as Error).message)
  }
}

export default function Library() {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get("q") ?? "")
  const [open, setOpen] = useState<number | null>(null)
  const navigate = useNavigate()
  const { status: app } = useStatus()
  const status = params.get("status") ?? ""
  const page = Number(params.get("page") ?? 1)
  const url = `/api/songs?${new URLSearchParams({ ...Object.fromEntries(params), size: "50" })}`
  const { data, reload } = usePoll<Page>(url, 15000)
  const stats = usePoll<{ by_status: Record<Status, number>; songs: number }>("/api/stats", 15000).data

  useEffect(() => {
    const timer = setTimeout(() => {
      if ((params.get("q") ?? "") === query) return
      const next = new URLSearchParams(params)
      if (query) next.set("q", query); else next.delete("q")
      next.delete("page")
      setParams(next, { replace: true })
    }, 250)
    return () => clearTimeout(timer)
  }, [query, params, setParams])

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    if (key !== "page") next.delete("page")
    setParams(next)
  }
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1
  const extra = ["language_code", "format"].filter((k) => params.get(k))

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={status || "all"} onValueChange={(v) => set("status", v === "all" ? "" : v)}>
          <TabsList className="h-9">
            <TabsTrigger value="all">All <span className="ml-1.5 text-muted-foreground tabular-nums">{stats?.songs ?? ""}</span></TabsTrigger>
            {STATUS_ORDER.filter((k) => k !== "instrumental").map((k) => (
              <TabsTrigger key={k} value={k}>
                {STATUS[k].label}<span className="ml-1.5 text-muted-foreground tabular-nums">{stats?.by_status[k] ?? ""}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="relative ml-auto w-72">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, artist, album" className="pl-8" />
        </div>
      </div>
      {extra.length > 0 && (
        <div className="flex gap-2">
          {extra.map((k) => (
            <Button key={k} variant="secondary" size="sm" onClick={() => set(k, "")}>
              {k === "format" ? "Format" : "Language"}: {params.get(k) || "unknown"} <X />
            </Button>
          ))}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[42%]">Song</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Lyrics</TableHead>
              <TableHead>Language</TableHead>
              <TableHead className="text-right">Length</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {!data && [0, 1, 2, 3, 4, 5].map((i) => (
              <TableRow key={i}><TableCell colSpan={6}><Skeleton className="h-8" /></TableCell></TableRow>
            ))}
            {data?.songs.length === 0 && (
              <TableRow><TableCell colSpan={6} className="h-32 text-center text-muted-foreground">No songs here.</TableCell></TableRow>
            )}
            {data?.songs.map((song) => (
              <TableRow key={song.id} className="cursor-pointer" onClick={() => setOpen(song.id)}>
                <TableCell className="max-w-0">
                  <div className="truncate font-medium">{song.title}</div>
                  <div className="truncate text-xs text-muted-foreground">{song.artist}{song.album ? ` · ${song.album}` : ""}</div>
                </TableCell>
                <TableCell>
                  <StatusBadge status={song.status} />
                  {song.job && ["queued", "running"].includes(song.job) && <div className="mt-1 text-[11px] text-sky-400">AI {song.job}</div>}
                  {song.status === "ai_unchecked" && !!song.to_check && <div className="mt-1 text-[11px] text-muted-foreground">{song.to_check} lines unsure</div>}
                </TableCell>
                <TableCell><div className="flex flex-wrap gap-1">{song.formats.map((f) => <FormatBadge key={f} format={f} />)}</div></TableCell>
                <TableCell><div className="flex flex-wrap gap-1">{song.languages.map((l) => <LanguageBadge key={l.code} name={l.name} />)}</div></TableCell>
                <TableCell className="text-right text-muted-foreground tabular-nums">{length(song.duration)}</TableCell>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="size-8"><MoreHorizontal /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem disabled={song.status === "missing"} onClick={() => navigate(`/editor/${song.id}`)}>
                        <PenLine />Open in editor</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => searchNow(song.id).then(reload)}><Wand2 />Search LRCLIB now</DropdownMenuItem>
                      <DropdownMenuItem disabled={!app?.gpu_online || song.status === "missing"} onClick={() => aiSync(song.id).then(reload)}>
                        <Sparkles />AI sync{!app?.gpu_online && <span className="ml-auto text-xs text-muted-foreground">no GPU</span>}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {data && data.total > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{data.total.toLocaleString()} songs</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="icon" className="size-8" disabled={page <= 1} onClick={() => set("page", String(page - 1))}><ChevronLeft /></Button>
            <span className="tabular-nums">Page {page} of {pages}</span>
            <Button variant="outline" size="icon" className="size-8" disabled={page >= pages} onClick={() => set("page", String(page + 1))}><ChevronRight /></Button>
          </div>
        </div>
      )}

      <SongSheet songId={open} onClose={() => { setOpen(null); reload() }} />
      {!app?.gpu_online && data?.songs.some((s) => s.status === "plain") && (
        <p className="text-xs text-muted-foreground">
          Plain lyrics can become synced ones with AI sync, which needs a <Link to="/workers" className="underline underline-offset-2">GPU worker</Link>.
        </p>
      )}
    </div>
  )
}

export function AiSyncButton({ id, disabled, onDone }: { id: number; disabled?: string; onDone?: () => void }) {
  const button = (
    <Button variant="outline" size="sm" disabled={!!disabled} onClick={() => aiSync(id).then(onDone)}><Sparkles />AI sync</Button>
  )
  if (!disabled) return button
  return <Tooltip><TooltipTrigger asChild><span tabIndex={0}>{button}</span></TooltipTrigger><TooltipContent>{disabled}</TooltipContent></Tooltip>
}
