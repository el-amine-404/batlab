import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowLeft, Check, CheckCheck, ChevronRight, Copy, GripVertical, Keyboard, Pause, Play, Trash2 } from "lucide-react"
import { Link, useParams } from "react-router"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import { Progress } from "@/components/ui/progress"
import { api } from "@/lib/api"
import { useLyricsChanged } from "@/lib/queries"
import { clock } from "@/lib/format"
import { cn } from "@/lib/utils"
import { SoundMenu } from "@/editor/SoundMenu"
import { Stage } from "@/editor/Stage"
import { Overview, Waveform, type Layer, type Line, type View } from "@/editor/Timeline"

type Draft = { lines: Line[]; dirty: boolean }
type EditorData = {
  lines: Line[]; origin: string; ai_state: string | null; has_voice: boolean; read_only: boolean
  song: { id: number; artist: string; album: string; title: string; duration: number; rel: string; root: string }
}
type Peaks = { rate: number; peaks: number[] }

const STEP = 0.05, FINE = 0.01
const NO_PEAKS: Peaks = { rate: 200, peaks: [] }

const SHORTCUTS: [string[], string][] = [
  [["Space"], "Play / pause where you are"],
  [["←", "→"], "Move 50 ms (with Shift: 10 ms)"],
  [["↑", "↓"], "Choose a line (the position stays)"],
  [["S"], "Snap to where the voice starts"],
  [["Enter"], "The chosen line starts here, next line"],
  [["Ctrl", "Enter"], "Mark every line checked (timings stay)"],
  [["E"], "Edit the chosen line's text"],
  [["N"], "New line at the position"],
  [["Delete"], "Remove the chosen line"],
  [["Ctrl", "D"], "Duplicate the chosen line at the position"],
  [["Drag ⋮⋮"], "Move a line's words; the times stay in place"],
  [["V"], "Hear the song or the voice alone"],
  [["Wheel"], "Scroll the waveform (Ctrl: zoom)"],
  [["Ctrl", "Z"], "Undo"],
  [["Ctrl", "S"], "Save"],
  [["?"], "This list"],
]

const draftKey = (id: string) => `stanzarr-editor:${id}`
function loadDraft(id: string): Draft | null {
  try { return JSON.parse(localStorage.getItem(draftKey(id)) || "null") } catch { return null }
}
function storeDraft(id: string, draft: Draft) {
  try { localStorage.setItem(draftKey(id), JSON.stringify(draft)) } catch { /* private window */ }
}

export default function Editor() {
  const { id: songId = "" } = useParams()
  const changed = useLyricsChanged()
  const [meta, setMeta] = useState<EditorData | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [dirty, setDirty] = useState(false)
  const [mixPeaks, setMixPeaks] = useState<Peaks>(NO_PEAKS)
  const [voicePeaks, setVoicePeaks] = useState<Peaks>(NO_PEAKS)
  const [shown, setShown] = useState<string[]>(["mix", "voice"])
  const [source, setSource] = useState<"mix" | "voice">("mix")
  const [sel, setSel] = useState(0)
  const [marker, setMarker] = useState(0) // the position: where the song is, playing or paused
  const [playing, setPlaying] = useState(false)
  const [view, setView] = useState<View>({ from: 0, span: 8 })
  const [editing, setEditing] = useState<string | null>(null)
  const [help, setHelp] = useState(false)
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const undo = useRef<Line[][]>([])
  const list = useRef<HTMLDivElement>(null)
  const markerRef = useRef(0)
  markerRef.current = marker

  const song = meta?.song
  const hasVoice = !!meta?.has_voice
  const duration = Math.max(mixPeaks.peaks.length / mixPeaks.rate, voicePeaks.peaks.length / voicePeaks.rate)
  const layers = useMemo<Layer[]>(() => [
    ...(shown.includes("mix") ? [{ ...mixPeaks, color: "#3f3f46" }] : []),
    ...(shown.includes("voice") ? [{ ...voicePeaks, color: "#2dd4bf" }] : []),
  ], [shown, mixPeaks, voicePeaks])

  useEffect(() => {
    if (!songId) return
    let live = true
    audio.current?.pause()
    setMixPeaks(NO_PEAKS)
    setVoicePeaks(NO_PEAKS)
    api.editor(Number(songId)).then((answer) => {
      const s = answer as unknown as EditorData
      if (!live) return
      setMeta(s)
      if (!s.has_voice) { setSource("mix"); setShown(["mix"]) }
      const draft = loadDraft(songId)
      const start = [...(draft?.dirty ? draft.lines : s.lines)].sort((a, b) => a.t - b.t)
      setLines(start)
      setDirty(!!draft?.dirty)
      setSel(0)
      const t = start[0]?.t ?? 0
      setMarker(t)
      setView({ from: Math.max(0, t - 2), span: 8 })
      undo.current = []
      if (s.has_voice) api.peaks(Number(songId), "voice").then((p) => { if (live) setVoicePeaks(p) })
    }).catch((e) => toast.error(`Could not open the song: ${e.message}`))
    api.peaks(Number(songId), "mix").then((p) => { if (live) setMixPeaks(p) })
    return () => { live = false }
  }, [songId])

  // The audio follows the song and what you chose to hear; the position is kept.
  useEffect(() => {
    const a = audio.current
    if (!a || !songId) return
    const resume = !a.paused
    a.src = api.audioUrl(Number(songId), source)
    a.addEventListener("loadedmetadata", () => {
      a.currentTime = markerRef.current
      if (resume) void a.play()
    }, { once: true })
  }, [source, songId])

  // While playing, the position is the song's.
  useEffect(() => {
    let frame = 0
    const loop = () => {
      const a = audio.current
      if (a && !a.paused) setMarker(a.currentTime)
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(frame)
  }, [])

  // Keep the position in sight: turn the page when it leaves the view.
  useEffect(() => {
    setView((v) => {
      if (marker >= v.from && marker <= v.from + v.span * 0.9) return v
      return { ...v, from: Math.max(0, Math.min(Math.max(0, duration - v.span), marker - v.span * 0.25)) }
    })
  }, [marker, duration])

  const change = useCallback((next: Line[]) => {
    undo.current.push(lines)
    if (undo.current.length > 200) undo.current.shift()
    setLines(next)
    setDirty(true)
    storeDraft(songId, { lines: next, dirty: true })
  }, [lines, songId])

  const seek = useCallback((t: number) => {
    const at = Math.max(0, Math.min(duration || Infinity, Math.round(t * 1000) / 1000))
    if (audio.current) audio.current.currentTime = at
    setMarker(at)
  }, [duration])

  const toggle = useCallback(() => {
    const a = audio.current
    if (!a) return
    if (a.paused) { a.currentTime = markerRef.current; void a.play() } else a.pause()
  }, [])

  /** To where the voice comes in nearest the position: a jump of 9 dB or
   *  more above the quietest point of the 100 ms before (the start of a word
   *  or a syllable, even in rap where the voice never stops), to at least
   *  -30 dB, from 0.5 s before to 1 s after. Lands 20 ms early, so the line
   *  lights up with the voice. */
  const snap = useCallback(() => {
    // The voice alone is best; without an AI sync there is only the song.
    const { rate, peaks: p } = voicePeaks.peaks.length ? voicePeaks : mixPeaks
    if (!p.length) return
    const raw = p.map((v) => (v > 0 ? 20 * Math.log10(v) : -120))
    const db = (i: number) => (raw[i - 1] + raw[i] + raw[i + 1]) / 3 // 15 ms smoothing
    const quietest = (i: number) => { let m = Infinity; for (let j = i - 20; j < i; j++) m = Math.min(m, db(j)); return m }
    const at = markerRef.current
    let best = -1
    for (let i = Math.max(22, Math.floor((at - 0.5) * rate)); i < Math.min(p.length - 2, (at + 1) * rate); i++) {
      if (db(i) < -30 || db(i) - quietest(i) < 9 || db(i - 1) - quietest(i - 1) >= 9) continue
      let j = i
      while (j > i - 20 && db(j - 1) < db(j)) j-- // back to where the rise began
      if (best < 0 || Math.abs(j / rate - at) < Math.abs(best / rate - at)) best = j
    }
    if (best < 0) return void toast("No voice start near here")
    seek(best / rate - 0.02)
  }, [voicePeaks, mixPeaks, seek])

  /** The chosen line starts at the position. Lines stay in time order (a
   *  player needs them so), and the line after it in time is chosen next. */
  const commit = useCallback(() => {
    const t = Math.round(marker * 1000) / 1000
    const moved = { ...lines[sel], t, fixed: true }
    const next = lines.map((l, i) => (i === sel ? moved : l)).sort((a, b) => a.t - b.t)
    change(next)
    setSel(Math.min(next.length - 1, next.indexOf(moved) + 1))
  }, [marker, lines, sel, change])

  const remove = useCallback((at: number = sel) => {
    if (!lines[at]) return
    const gone = lines[at].s
    change(lines.filter((_, i) => i !== at))
    setSel(Math.max(0, Math.min(lines.length - 2, at)))
    toast(`Removed "${gone.length > 40 ? gone.slice(0, 40) + "…" : gone}"`, { action: { label: "Undo", onClick: () => {
      const prev = undo.current.pop()
      if (prev) { setLines(prev); storeDraft(songId, { lines: prev, dirty: true }) }
    } } })
  }, [lines, sel, change, songId])

  /** A copy of a line at the position (or halfway to the next line when the
   *  position is the line itself): a chorus the lyrics wrote once. */
  const duplicate = useCallback((at: number = sel) => {
    const l = lines[at]
    if (!l) return
    let t = Math.round(marker * 1000) / 1000
    if (Math.abs(t - l.t) < 0.001) t = Math.round(((l.t + (lines[at + 1]?.t ?? l.t + 4)) / 2) * 1000) / 1000
    const copy: Line = { t, s: l.s, u: false, fixed: false }
    const next = [...lines, copy].sort((a, b) => a.t - b.t)
    change(next)
    setSel(next.indexOf(copy))
  }, [marker, lines, sel, change])

  /** Dragging moves words between the time slots: the times stay in order,
   *  in place; the words of the dragged line go to the slot dropped on and
   *  the lines between shift by one. */
  const moveWords = useCallback((from: number, to: number) => {
    if (from === to) return
    const words = lines.map((l) => l.s)
    const [moved] = words.splice(from, 1)
    words.splice(to, 0, moved)
    change(lines.map((l, i) => ({ ...l, s: words[i] })))
    setSel(to)
  }, [lines, change])

  /** A new, empty line at the position, open for typing. */
  const insert = useCallback(() => {
    const added: Line = { t: Math.round(marker * 1000) / 1000, s: "", u: false, fixed: true }
    const next = [...lines, added].sort((a, b) => a.t - b.t)
    change(next)
    setSel(next.indexOf(added))
    setEditing("")
  }, [marker, lines, change])

  /** Every line checked as it is: the song was listened through and is right. */
  const checkAll = useCallback(() => {
    if (lines.every((l) => l.fixed)) return
    change(lines.map((l) => ({ ...l, fixed: true })))
    toast("Every line marked checked", { action: { label: "Undo", onClick: () => {
      const prev = undo.current.pop()
      if (prev) { setLines(prev); storeDraft(songId, { lines: prev, dirty: true }) }
    } } })
  }, [lines, change, songId])

  const save = useCallback(async () => {
    if (meta?.read_only) return void toast.error("stanzarr is read-only: nothing is written")
    try {
      await api.saveLyrics(Number(songId), lines.map(({ t, s, fixed, u }) => ({ t, s, fixed, u })))
      changed(Number(songId))
    } catch (e) {
      return void toast.error(`Not saved: ${(e as Error).message}`)
    }
    setDirty(false)
    storeDraft(songId, { lines, dirty: false })
    toast.success("Saved beside the song")
  }, [lines, songId, meta])

  // The editor's keys come first, so a focused control cannot take them.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing !== null || e.target instanceof HTMLInputElement || help) return
      const mod = e.ctrlKey || e.metaKey
      const k = e.key
      const handled = () => { e.preventDefault(); e.stopPropagation() }
      if (mod && k.toLowerCase() === "s") { handled(); void save() }
      else if (mod && k.toLowerCase() === "z") {
        handled()
        const prev = undo.current.pop()
        if (prev) { setLines(prev); setDirty(true); storeDraft(songId, { lines: prev, dirty: true }) }
      }
      else if (mod && k === "Enter") { handled(); checkAll() }
      else if (mod && k.toLowerCase() === "d") { handled(); duplicate(sel) }
      else if (mod || e.altKey) return
      else if (k === " ") { handled(); toggle() }
      else if (k === "ArrowUp") { handled(); setSel((s) => Math.max(0, s - 1)) }
      else if (k === "ArrowDown") { handled(); setSel((s) => Math.min(lines.length - 1, s + 1)) }
      else if (k === "ArrowLeft") { handled(); seek(markerRef.current - (e.shiftKey ? FINE : STEP)) }
      else if (k === "ArrowRight") { handled(); seek(markerRef.current + (e.shiftKey ? FINE : STEP)) }
      else if (k === "Enter") { handled(); commit() }
      else if (k === "s" || k === "S") { handled(); snap() }
      else if (k === "e" || k === "E") { handled(); setEditing(lines[sel]?.s ?? "") }
      else if (k === "Delete" || k === "Backspace") { handled(); remove(sel) }
      else if (k === "n" || k === "N") { handled(); insert() }
      else if (k === "v" || k === "V") { handled(); if (hasVoice) setSource((s) => (s === "mix" ? "voice" : "mix")) }
      else if (k === "?") { handled(); setHelp(true) }
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [editing, help, sel, lines, songId, hasVoice, save, toggle, seek, commit, snap, checkAll, remove, insert, duplicate])

  const current = useMemo(() => {
    let c = -1
    lines.forEach((l, i) => { if (l.t <= marker) c = i })
    return c
  }, [lines, marker])

  useEffect(() => {
    list.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" })
  }, [sel])
  useEffect(() => {
    if (playing) list.current?.querySelector(`[data-i="${current}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [current, playing])

  const line = lines[sel]
  const delta = line ? marker - line.t : 0
  const unsure = lines.filter((l) => l.u).length
  const checked = lines.filter((l) => l.u && l.fixed).length

  return (
    <div className="flex h-svh bg-background text-sm text-foreground select-none">
      <audio ref={audio} preload="auto" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />

      <main className="flex min-w-0 flex-1 flex-col">
        {/* where: the file, and where it stands */}
        <header className="flex h-11 items-center gap-3 border-b px-3">
          <Button variant="ghost" size="icon" className="size-7 text-muted-foreground" tabIndex={-1} asChild>
            <Link to={`/songs/${songId}`} aria-label="Back to the song"><ArrowLeft className="size-4" /></Link>
          </Button>
          <nav className="flex min-w-0 items-center gap-1 text-muted-foreground" aria-label="Path">
            {(song ? song.rel : "").split("/").map((part, i, all) => (
              <span key={i} className="flex min-w-0 items-center gap-1">
                {i > 0 && <ChevronRight className="size-3.5 shrink-0 opacity-50" />}
                <span className={cn("truncate", i === all.length - 1 && "text-foreground")}>{part}</span>
              </span>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-4">
            {unsure > 0 && (
              <div className="flex items-center gap-2.5 text-muted-foreground">
                <Progress value={(checked / unsure) * 100} className="h-1.5 w-24 [&>div]:bg-green-500" />
                <span><span className="text-foreground tabular-nums">{checked}</span> of {unsure} unsure checked</span>
              </div>
            )}
            {lines.some((l) => !l.fixed) && (
              <Button size="sm" variant="ghost" className="text-muted-foreground hover:text-foreground" tabIndex={-1}
                onClick={checkAll} title="Every line checked as it is (Ctrl+Enter)">
                <CheckCheck className="size-4" />Mark all checked
              </Button>
            )}
            <Button size="sm" variant={dirty ? "default" : "secondary"} disabled={!dirty} onClick={() => void save()} tabIndex={-1}>
              {dirty ? "Save" : <><Check className="size-4" />Saved</>}
            </Button>
          </div>
        </header>

        {/* how it plays */}
        <Stage lines={lines} current={current} position={marker} />

        {/* where you are */}
        <div className="flex h-12 items-center gap-4 border-t px-4 tabular-nums">
          <Button size="icon" variant="secondary" className="size-8 shrink-0 rounded-full" onClick={toggle} tabIndex={-1}
            aria-label={playing ? "Pause" : "Play"}>
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </Button>
          <span className="font-medium">{clock(marker)}<span className="font-normal text-muted-foreground"> / {clock(duration)}</span></span>
          {line && (
            <>
              <span className="h-4 w-px bg-border" />
              <span className="text-muted-foreground">line {sel + 1} starts <span className="font-medium text-foreground">{clock(line.t)}</span></span>
              {Math.abs(delta) >= 0.0005 && (
                <>
                  <span className="h-4 w-px bg-border" />
                  <span className="text-muted-foreground"><span className="font-medium text-foreground">{delta > 0 ? "+" : "−"}{Math.abs(delta).toFixed(3)} s</span> away</span>
                </>
              )}
            </>
          )}
          <div className="ml-auto">
            <SoundMenu source={source} onSource={setSource} shown={shown} onShown={setShown} hasVoice={hasVoice} />
          </div>
        </div>

        <div className="space-y-2 px-4 pb-3">
          <Waveform layers={layers} duration={duration} lines={lines} selected={sel}
            marker={marker} view={view} onView={setView} onSeek={seek} />
          <Overview layers={layers} duration={duration} lines={lines} selected={sel}
            marker={marker} view={view} onView={setView} />
        </div>

        {/* the lines */}
        <div ref={list} className="flex-1 overflow-y-auto border-t px-2 py-2">
          {lines.map((l, i) => (
            <div key={i} data-i={i} onClick={() => { setSel(i); seek(l.t) }}
              onDoubleClick={() => { setSel(i); setEditing(l.s) }}
              onDragOver={(e) => { if (drag) { e.preventDefault(); if (drag.to !== i) setDrag({ ...drag, to: i }) } }}
              onDrop={(e) => { e.preventDefault(); if (drag) moveWords(drag.from, drag.to); setDrag(null) }}
              className={cn("group relative flex cursor-pointer items-center gap-3 rounded-md px-3 py-1.5 transition-colors",
                drag && drag.from !== drag.to && drag.to === i && (drag.to < drag.from
                  ? "before:absolute before:inset-x-2 before:-top-px before:h-0.5 before:rounded before:bg-primary"
                  : "after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded after:bg-primary"),
                drag?.from === i && "opacity-40",
                i === current ? "bg-green-500/10 text-foreground" : "text-muted-foreground hover:bg-accent/40",
                i === sel && "ring-1 ring-foreground/60")}>
              <span draggable title="Drag to move these words to another line's time"
                onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); setDrag({ from: i, to: i }) }}
                onDragEnd={() => setDrag(null)} onClick={(e) => e.stopPropagation()}
                className="-ml-1.5 cursor-grab text-muted-foreground opacity-0 group-hover:opacity-60 hover:opacity-100 active:cursor-grabbing">
                <GripVertical className="size-3.5" />
              </span>
              <span className={cn("size-1.5 shrink-0 rounded-full", l.fixed ? "bg-green-500" : l.u ? "bg-amber-500" : "bg-transparent")} />
              <span className="w-7 shrink-0 text-right text-xs tabular-nums opacity-50">{i + 1}</span>
              <span className="w-20 shrink-0 text-xs tabular-nums">{clock(l.t)}</span>
              {editing !== null && i === sel ? (
                <Input autoFocus value={editing} className="h-7 select-text"
                  onChange={(e) => setEditing(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      const text = editing.trim()
                      change(text || l.s ? lines.map((x, j) => (j === sel ? { ...x, s: text || x.s } : x)) : lines.filter((_, j) => j !== sel))
                      setEditing(null)
                    }
                    if (e.key === "Escape") {
                      if (!l.s) setLines((all) => all.filter((_, j) => j !== sel)) // an abandoned new line
                      setEditing(null)
                    }
                  }}
                  onBlur={() => { if (!l.s) setLines((all) => all.filter((_, j) => j !== sel)); setEditing(null) }} />
              ) : <span className="flex-1 truncate">{l.s}</span>}
              {editing === null && (
                <button type="button" tabIndex={-1} aria-label="Duplicate this line" title="Duplicate at the position (Ctrl+D)"
                  onClick={(e) => { e.stopPropagation(); duplicate(i) }}
                  className="invisible shrink-0 rounded p-1 text-muted-foreground group-hover:visible hover:bg-accent hover:text-foreground">
                  <Copy className="size-3.5" />
                </button>
              )}
              {editing === null && (
                <button type="button" tabIndex={-1} aria-label="Remove this line" title="Remove this line (Delete)"
                  onClick={(e) => { e.stopPropagation(); remove(i) }}
                  className="invisible shrink-0 rounded p-1 text-muted-foreground group-hover:visible hover:bg-destructive/15 hover:text-destructive">
                  <Trash2 className="size-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>

        {/* the essentials; the rest behind ? */}
        <footer className="flex h-9 items-center gap-5 border-t px-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5"><Kbd>Space</Kbd>Play</span>
          <span className="flex items-center gap-1.5"><Kbd>←</Kbd><Kbd>→</Kbd>Move</span>
          <span className="flex items-center gap-1.5"><Kbd>S</Kbd>Snap to voice</span>
          <span className="flex items-center gap-1.5"><Kbd>Enter</Kbd>Set start</span>
          <span className="ml-auto flex items-center gap-4">
            <span className="flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-amber-500" />AI unsure</span>
            <span className="flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-green-500" />Checked</span>
            <button type="button" tabIndex={-1} onClick={() => setHelp(true)} className="flex items-center gap-1.5 hover:text-foreground">
              <Keyboard className="size-3.5" />All shortcuts<Kbd>?</Kbd>
            </button>
          </span>
        </footer>
      </main>

      <Dialog open={help} onOpenChange={setHelp}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>Shortcuts</DialogTitle></DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-2.5 text-sm">
            {SHORTCUTS.map(([keys, what]) => (
              <div key={what} className="contents">
                <dt className="flex gap-1">{keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</dt>
                <dd className="text-muted-foreground">{what}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </div>
  )
}
