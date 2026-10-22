import { useEffect, useRef } from "react"
import { clock } from "@/lib/format"

export type Line = { t: number; s: string; u: boolean; fixed?: boolean }
export type View = { from: number; span: number }

export type Layer = { peaks: number[]; rate: number; color: string }
type Common = { layers: Layer[]; duration: number; lines: Line[]; selected: number; marker: number }

const COLORS = { line: "#737373", unsure: "#f59e0b", fixed: "#22c55e", selected: "#fafafa", marker: "#22c55e" }

function lineColor(l: Line, selected: boolean) {
  return selected ? COLORS.selected : l.fixed ? COLORS.fixed : l.u ? COLORS.unsure : COLORS.line
}

function setup(el: HTMLCanvasElement) {
  const dpr = window.devicePixelRatio || 1
  const w = el.clientWidth, h = el.clientHeight
  el.width = w * dpr
  el.height = h * dpr
  const g = el.getContext("2d")!
  g.scale(dpr, dpr)
  g.clearRect(0, 0, w, h)
  return { g, w, h }
}

function drawLayers(g: CanvasRenderingContext2D, layers: Layer[], from: number, span: number, w: number, top: number, height: number) {
  for (const { peaks, rate, color } of layers) drawPeaks(g, peaks, rate, color, from, span, w, top, height)
}

function drawPeaks(g: CanvasRenderingContext2D, peaks: number[], rate: number, color: string, from: number, span: number, w: number, top: number, height: number) {
  g.fillStyle = color
  const first = from * rate, perPixel = (span * rate) / w
  for (let px = 0; px < w; px++) {
    let peak = 0
    const a = Math.floor(first + px * perPixel), b = Math.max(a + 1, Math.floor(first + (px + 1) * perPixel))
    for (let i = Math.max(0, a); i < b && i < peaks.length; i++) peak = Math.max(peak, peaks[i])
    // Decibels, like an audio editor: a soft start ("b", "s", a breath) shows
    // as a rise instead of a flat line. -42 dB and below is drawn as silence:
    // what Demucs leaves of the music in the voice track sits around -45 dB.
    const level = peak > 0 ? Math.min(1, Math.max(0, (20 * Math.log10(peak) + 42) / 42)) : 0
    const bar = Math.max(1, level * height)
    g.fillRect(px, top + (height - bar) / 2, 1, bar)
  }
}

/** The zoomed waveform: wheel scrolls, Ctrl + wheel zooms, click moves the
 *  position. Scrolling is drawn here, every frame, easing toward where the
 *  wheel asked; the page hears of it a few times a second and when it settles. */
export function Waveform({ layers, duration, lines, selected, marker, view, onView, onSeek }:
  Common & { view: View; onView: (v: View) => void; onSeek: (t: number) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const shown = useRef<View>(view) // what is drawn now
  const target = useRef<View>(view) // where the wheel is taking it
  const frame = useRef(0)
  const reported = useRef(0)
  const props = useRef({ layers, lines, selected, marker, duration, onView })
  props.current = { layers, lines, selected, marker, duration, onView }

  const paint = (v: View) => {
    const el = canvas.current
    if (!el) return
    const { layers, lines, selected, marker } = props.current
    const { g, w, h } = setup(el)
    const { from, span } = v
    const x = (t: number) => ((t - from) / span) * w
    const wave = h - 22

    const tick = span <= 10 ? 0.1 : span <= 30 ? 0.5 : 1
    const label = span <= 15 ? 1 : span <= 40 ? 5 : 10
    g.font = "500 11px \"Inter Variable\", system-ui, sans-serif"
    for (let t = Math.ceil(from / tick) * tick; t <= from + span; t += tick) {
      const major = Math.abs(t / label - Math.round(t / label)) < 1e-6
      g.fillStyle = major ? "#a3a3a3" : "#404040"
      g.fillRect(Math.round(x(t)), wave, 1, major ? 8 : 4)
      if (major) g.fillText(clock(Math.round(t)).slice(0, -4), x(t) + 3, h - 4)
    }

    drawLayers(g, layers, from, span, w, 6, wave - 12)

    lines.forEach((l, i) => {
      if (l.t < from || l.t > from + span) return
      const sel = i === selected
      g.fillStyle = lineColor(l, sel)
      g.globalAlpha = sel ? 1 : 0.8
      g.fillRect(Math.round(x(l.t)), 0, sel ? 2 : 1, wave)
      g.fillText(`${i + 1}`, x(l.t) + 4, 12)
      g.globalAlpha = 1
    })

    if (marker >= from && marker <= from + span) {
      g.fillStyle = COLORS.marker
      g.fillRect(x(marker) - 1, 0, 2, wave)
      g.beginPath()
      g.moveTo(x(marker) - 6, 0); g.lineTo(x(marker) + 6, 0); g.lineTo(x(marker), 8); g.fill()
    }
  }

  const clamp = (v: View): View => {
    const { duration } = props.current
    const span = Math.min(Math.max(1, duration || v.span), Math.max(1, v.span))
    return { span, from: Math.max(0, Math.min(Math.max(0, (duration || Infinity) - span), v.from)) }
  }

  const animate = () => {
    const a = shown.current, b = target.current
    const next = { from: a.from + (b.from - a.from) * 0.35, span: a.span + (b.span - a.span) * 0.35 }
    const done = Math.abs(b.from - next.from) < b.span / 4000 && Math.abs(b.span - next.span) < b.span / 4000
    shown.current = done ? b : next
    paint(shown.current)
    const now = performance.now()
    if (done || now - reported.current > 150) {
      reported.current = now
      props.current.onView(shown.current)
    }
    frame.current = done ? 0 : requestAnimationFrame(animate)
  }

  // The wheel: pixels from any mouse or touchpad (Firefox on Linux counts lines).
  useEffect(() => {
    const el = canvas.current!
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? el.clientWidth : 1
      const r = el.getBoundingClientRect()
      const t = target.current
      if (e.ctrlKey) {
        const ratio = (e.clientX - r.left) / r.width
        const at = t.from + ratio * t.span
        const span = t.span * Math.pow(1.0015, e.deltaY * unit)
        target.current = clamp({ span, from: at - ratio * span })
      } else {
        const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
        target.current = clamp({ ...t, from: t.from + ((d * unit) / r.width) * t.span })
      }
      if (!frame.current) frame.current = requestAnimationFrame(animate)
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => { el.removeEventListener("wheel", onWheel); cancelAnimationFrame(frame.current); frame.current = 0 }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A view set from outside (the position turning the page, the minimap)
  // replaces any scroll in progress.
  useEffect(() => {
    if (frame.current && Math.abs(view.from - shown.current.from) < 1e-6 && view.span === shown.current.span) return
    if (!frame.current) { shown.current = view; target.current = view }
    paint(shown.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, layers, lines, selected, marker])

  return (
    <canvas ref={canvas} className="h-40 w-full cursor-crosshair rounded-md bg-neutral-950"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect()
        const v = shown.current
        onSeek(v.from + ((e.clientX - r.left) / r.width) * v.span)
      }} />
  )
}

/** The whole song in one strip, like an editor's minimap: every line, the
 *  visible part as a box. Click or drag to look elsewhere. */
export function Overview({ layers, duration, lines, selected, marker, view, onView }:
  Common & { view: View; onView: (v: View) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const dragging = useRef(false)

  useEffect(() => {
    if (!duration) return
    const { g, w, h } = setup(canvas.current!)
    const x = (t: number) => (t / duration) * w
    drawLayers(g, layers, 0, duration, w, 2, h - 4)
    lines.forEach((l, i) => {
      g.fillStyle = lineColor(l, i === selected)
      g.globalAlpha = i === selected ? 1 : 0.6
      g.fillRect(Math.round(x(l.t)), 0, 1, h)
    })
    g.globalAlpha = 1
    g.fillStyle = "rgba(250,250,250,0.08)"
    g.fillRect(x(view.from), 0, Math.max(2, x(view.span)), h)
    g.strokeStyle = "rgba(250,250,250,0.5)"
    g.strokeRect(x(view.from) + 0.5, 0.5, Math.max(2, x(view.span)) - 1, h - 1)
    g.fillStyle = COLORS.marker
    g.fillRect(x(marker) - 1, 0, 2, h)
  }, [layers, duration, lines, selected, marker, view])

  const move = (clientX: number, el: HTMLCanvasElement) => {
    const r = el.getBoundingClientRect()
    const centre = ((clientX - r.left) / r.width) * duration
    onView({ ...view, from: Math.max(0, Math.min(Math.max(0, duration - view.span), centre - view.span / 2)) })
  }

  return (
    <canvas ref={canvas} className="h-10 w-full cursor-grab rounded-md bg-neutral-950 active:cursor-grabbing"
      onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); move(e.clientX, e.currentTarget) }}
      onPointerMove={(e) => { if (dragging.current) move(e.clientX, e.currentTarget) }}
      onPointerUp={() => { dragging.current = false }} />
  )
}
