import { useState } from "react"
import { Copy, Cpu, KeyRound } from "lucide-react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { api, type Worker } from "@/lib/api"
import { ago } from "@/lib/format"
import { usePoll } from "@/lib/status"
import { cn } from "@/lib/utils"

function Command({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border bg-muted/40 p-3 font-mono text-xs">
      <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all">{text}</pre>
      <Button variant="ghost" size="icon" className="size-7 shrink-0" aria-label="Copy"
        onClick={() => navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast.error("Copy failed"))}>
        <Copy className="size-3.5" />
      </Button>
    </div>
  )
}

export default function Workers() {
  const { data, reload } = usePoll<{ workers: Worker[]; key: string; key_fixed: boolean }>("/api/workers", 5000)
  const [shown, setShown] = useState(false)
  const server = window.location.origin
  const key = data?.key ?? "…"
  const gpu = data?.workers.some((w) => w.online && w.gpu)

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {!gpu && (
        <Alert>
          <Cpu />
          <AlertTitle>No GPU worker online: AI sync is disabled</AlertTitle>
          <AlertDescription>AI sync separates the voice and places every line with Whisper, which needs a graphics card (NVIDIA, 4 GB or more). Start a worker on a computer that has one.</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">Workers</CardTitle><CardDescription>Computers that lend their GPU to Stanzarr.</CardDescription></CardHeader>
        <CardContent className="space-y-2">
          {data?.workers.length === 0 && <p className="text-sm text-muted-foreground">No worker has connected yet.</p>}
          {data?.workers.map((w) => (
            <div key={w.name} className="flex items-center gap-3 rounded-lg border p-3">
              <span className={cn("size-2 rounded-full", w.online ? "bg-emerald-500" : "bg-zinc-600")} />
              <div className="min-w-0 flex-1">
                <div className="font-medium">{w.name}</div>
                <div className="text-xs text-muted-foreground">
                  {w.gpu ? `${w.gpu}${w.vram ? ` · ${(w.vram / 1024).toFixed(1)} GB` : ""}` : "no GPU detected: takes no AI jobs"} · v{w.version || "?"}
                </div>
              </div>
              <span className="text-xs text-muted-foreground">{w.online ? "online" : `seen ${ago(w.last_seen)}`}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Start a worker</CardTitle>
          <CardDescription>On a Linux computer with an NVIDIA GPU, Python 3.12 and ffmpeg. The models (about 5 GB) download on first run.</CardDescription></CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">1. Install the worker:</p>
          <Command text={`python3.12 -m venv ~/.stanzarr-worker && ~/.stanzarr-worker/bin/pip install "stanzarr-worker @ git+https://github.com/el-amine-404/stanzarr.git#subdirectory=worker"`} />
          <p className="text-muted-foreground">2. Run it (keep the key private: it lets a worker read your songs):</p>
          <Command text={`~/.stanzarr-worker/bin/stanzarr-worker --server ${server} --key ${shown ? key : "<key>"}`} />
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setShown((v) => !v)}><KeyRound />{shown ? "Hide the key" : "Show the key"}</Button>
            {!data?.key_fixed && (
              <Button variant="ghost" size="sm" onClick={() => api("/api/workers/key", { method: "POST" }).then(() => { toast("New key: restart your workers with it"); reload() })}>
                New key
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
