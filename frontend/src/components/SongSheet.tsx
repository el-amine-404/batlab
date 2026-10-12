import { Link } from "react-router-dom"
import { FileText, PenLine, Wand2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { FormatBadge, LanguageBadge, StatusBadge } from "@/components/Badges"
import type { EventRow, Job, LyricsFile, Status } from "@/lib/api"
import { ago, length, SOURCE } from "@/lib/format"
import { usePoll, useStatus } from "@/lib/status"
import { AiSyncButton, searchNow } from "@/pages/Library"

type Detail = {
  song: { id: number; artist: string; album: string; title: string; duration: number; rel: string; root: string; status: Status }
  files: LyricsFile[]; attempt: { at: number; result: string } | null; jobs: Job[]; events: EventRow[]
  has_voice: boolean; has_text: boolean
}

const AI_STATE: Record<string, string> = {
  unchecked: "not checked yet", checked: "checked by a person", corrected: "corrected by a person",
}

export function SongSheet({ songId, onClose }: { songId: number | null; onClose: () => void }) {
  const { data, reload } = usePoll<Detail>(songId ? `/api/songs/${songId}` : null, 5000)
  const { status } = useStatus()
  const detail = data && data.song.id === songId ? data : null
  const aiBlocked = !status?.gpu_online ? "Needs a GPU worker online (see Workers)"
    : !detail?.has_text ? "Needs plain lyrics first: AI sync places known words" : ""

  return (
    <Sheet open={songId !== null} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        {!detail ? <div className="space-y-3 p-4"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-24" /></div> : (
          <>
            <SheetHeader>
              <SheetTitle className="pr-6">{detail.song.title}</SheetTitle>
              <SheetDescription>{detail.song.artist}{detail.song.album ? ` · ${detail.song.album}` : ""} · {length(detail.song.duration)}</SheetDescription>
            </SheetHeader>
            <div className="space-y-6 px-4 pb-6">
              <div className="flex items-center justify-between">
                <StatusBadge status={detail.song.status} />
                <span className="truncate pl-4 text-xs text-muted-foreground" title={`${detail.song.root}/${detail.song.rel}`}>{detail.song.rel}</span>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" asChild={detail.song.status !== "missing"} disabled={detail.song.status === "missing"}>
                  {detail.song.status !== "missing" ? <Link to={`/editor/${detail.song.id}`}><PenLine />Open in editor</Link> : <span><PenLine />Open in editor</span>}
                </Button>
                <Button variant="outline" size="sm" onClick={() => searchNow(detail.song.id).then(reload)}><Wand2 />Search LRCLIB</Button>
                <AiSyncButton id={detail.song.id} disabled={aiBlocked} onDone={reload} />
              </div>

              <section className="space-y-2">
                <h3 className="text-sm font-medium">Lyrics files</h3>
                {detail.files.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
                {detail.files.map((f) => (
                  <div key={f.id} className="space-y-1.5 rounded-lg border p-3">
                    <div className="flex items-center gap-2">
                      <FileText className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-sm">{f.name}</span>
                      <FormatBadge format={f.format} synced={!!f.synced} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span>{f.synced ? "synced" : "plain"} · {f.lines} lines</span>
                      <span>{SOURCE[f.source]}{f.ai_state ? `, ${AI_STATE[f.ai_state]}` : ""}</span>
                      {f.language && <LanguageBadge name={f.language_name} />}
                      {f.source === "ai" && f.unsure > 0 && <span className="text-amber-400">{Math.max(0, f.unsure - f.checked)} of {f.unsure} unsure lines left</span>}
                    </div>
                  </div>
                ))}
              </section>

              <Separator />
              <section className="space-y-1.5 text-sm">
                <h3 className="font-medium">LRCLIB</h3>
                <p className="text-muted-foreground">{detail.attempt ? `Asked ${ago(detail.attempt.at)}: ${detail.attempt.result}` : "Not asked yet"}</p>
              </section>

              {detail.jobs.length > 0 && (
                <section className="space-y-1.5 text-sm">
                  <h3 className="font-medium">AI sync</h3>
                  {detail.jobs.map((j) => (
                    <p key={j.id} className="text-muted-foreground">{j.status} {j.stage && `· ${j.stage}`} {j.error && `· ${j.error}`} · {ago(j.created_at)}</p>
                  ))}
                </section>
              )}

              {detail.events.length > 0 && (
                <section className="space-y-1.5 text-sm">
                  <h3 className="font-medium">History</h3>
                  {detail.events.map((e) => (
                    <p key={e.id} className="text-muted-foreground"><span className="mr-2 text-xs">{ago(e.at)}</span>{e.message}</p>
                  ))}
                </section>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
