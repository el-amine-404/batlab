import { RotateCcw, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, type Job } from "@/lib/api"
import { ago } from "@/lib/format"
import { usePoll } from "@/lib/status"
import { cn } from "@/lib/utils"

const TONE: Record<Job["status"], string> = {
  queued: "text-muted-foreground", running: "text-sky-400", done: "text-emerald-400", failed: "text-rose-400", cancelled: "text-zinc-500",
}

export default function Queue() {
  const { data, reload } = usePoll<Job[]>("/api/jobs", 3000)
  const act = (path: string, method: string, done: string) =>
    api(path, { method }).then(() => { toast(done); reload() }).catch((e) => toast.error(e.message))

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <p className="text-sm text-muted-foreground">Songs waiting for, or done with, AI sync. A worker with a GPU takes them one by one.</p>
      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Song</TableHead><TableHead>Status</TableHead><TableHead>Worker</TableHead><TableHead>Queued</TableHead><TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {!data && <TableRow><TableCell colSpan={5}><Skeleton className="h-8" /></TableCell></TableRow>}
            {data?.length === 0 && <TableRow><TableCell colSpan={5} className="h-32 text-center text-muted-foreground">Nothing queued.</TableCell></TableRow>}
            {data?.map((j) => (
              <TableRow key={j.id}>
                <TableCell><div className="font-medium">{j.title}</div><div className="text-xs text-muted-foreground">{j.artist}</div></TableCell>
                <TableCell>
                  <span className={cn("text-sm font-medium", TONE[j.status])}>{j.status}</span>
                  {(j.stage || j.error) && <div className="max-w-xs truncate text-xs text-muted-foreground" title={j.error || j.stage}>{j.error || j.stage}</div>}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">{j.worker || "–"}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{ago(j.created_at)}</TableCell>
                <TableCell className="text-right">
                  {["queued", "running"].includes(j.status) && (
                    <Button variant="ghost" size="sm" onClick={() => act(`/api/jobs/${j.id}`, "DELETE", "Cancelled")}><X />Cancel</Button>
                  )}
                  {["failed", "cancelled"].includes(j.status) && (
                    <Button variant="ghost" size="sm" onClick={() => act(`/api/jobs/${j.id}/retry`, "POST", "Queued again")}><RotateCcw />Retry</Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
