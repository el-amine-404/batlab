import { useEffect, useRef } from "react";

/**
 * What the sign-in screen's left half shows, slowly: each app's things drifting
 * at different depths (0 far, 1 near: size, speed, light), fading in and out
 * at the edges into a soft glow, never a hard line.
 *   post: feed cards (a picture, two lines of text) rising, as a feed scrolls (subfeed);
 *   note: notes falling into the glow (songdrop);
 *   clip: tilted video thumbnails with a play button, rising (clipseerr);
 *   line: lyric lines with their time in front, rising as a song's lines scroll, a few lit (stanzarr).
 * A canvas, about 60 drawings a second while the tab is seen; paused when it
 * isn't, one still drawing for whoever asks for less motion. The same file in
 * each app (videodrop's is FallingFrames): only the kind and colours differ.
 */
export type DriftKind = "post" | "note" | "clip" | "line";

/** Tiles at once, by kind. */
const COUNT: Record<DriftKind, number> = { post: 16, note: 26, clip: 18, line: 22 };

type Thing = { x: number; y: number; depth: number; sway: number; phase: number; variant: number; tilt: number };

function thing(width: number, height: number, rising: boolean, anywhere: boolean): Thing {
  const edge = rising ? height + 60 + Math.random() * height * 0.3 : -60 - Math.random() * height * 0.3;
  return {
    x: Math.random() * width,
    y: anywhere ? Math.random() * height : edge,
    depth: Math.random(),
    sway: 6 + Math.random() * 14,
    phase: Math.random() * Math.PI * 2,
    variant: Math.random(),
    tilt: (Math.random() - 0.5) * 0.5,
  };
}

/** colour: "r,g,b" of the strokes; light: "r,g,b" of what is lit (the play sign, a picture). */
export function Drift({ kind, colour, light, className }: { kind: DriftKind; colour: string; light: string; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rising = kind !== "note";
    let width = 0;
    let height = 0;
    let things: Thing[] = [];
    let frame = 0;
    let last = performance.now();

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (!things.length) things = Array.from({ length: COUNT[kind] }, () => thing(width, height, rising, true));
    };

    const card = (x: number, y: number, size: number, t: Thing) => {
      const w = size * 3.4;
      const h = size * 1.15;
      const left = x - w / 2;
      const top = y - h / 2;
      context.fillStyle = `rgba(${colour},0.12)`;
      context.strokeStyle = `rgba(${colour},0.8)`;
      context.beginPath();
      context.roundRect(left, top, w, h, size * 0.2);
      context.fill();
      context.stroke();
      // Its picture, and two lines of text, the second shorter.
      const pad = h * 0.18;
      const pic = h - pad * 2;
      context.fillStyle = `rgba(${light},${0.35 + t.variant * 0.3})`;
      context.beginPath();
      context.roundRect(left + pad, top + pad, pic, pic, size * 0.1);
      context.fill();
      context.fillStyle = `rgba(${colour},0.7)`;
      const textLeft = left + pad * 2 + pic;
      const room = w - (textLeft - left) - pad;
      context.fillRect(textLeft, top + pad + pic * 0.12, room * (0.7 + t.variant * 0.3), Math.max(1.5, pic * 0.18));
      context.fillRect(textLeft, top + pad + pic * 0.58, room * (0.35 + t.variant * 0.3), Math.max(1.5, pic * 0.18));
    };

    const note = (x: number, y: number, size: number, t: Thing, now: number) => {
      context.save();
      context.translate(x, y);
      context.rotate(t.tilt + Math.sin(now / 1800 + t.phase) * 0.15);
      context.fillStyle = `rgba(${t.variant < 0.3 ? light : colour},0.9)`;
      context.font = `${Math.round(size * 1.6)}px ui-sans-serif, system-ui, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(t.variant < 0.5 ? "♪" : t.variant < 0.8 ? "♫" : "♬", 0, 0);
      context.restore();
    };

    const clip = (x: number, y: number, size: number, t: Thing) => {
      const w = size * 1.9;
      const h = w * 0.5625;
      context.save();
      context.translate(x, y);
      context.rotate(t.tilt * 0.6);
      context.fillStyle = `rgba(${colour},0.14)`;
      context.strokeStyle = `rgba(${colour},0.85)`;
      context.beginPath();
      context.roundRect(-w / 2, -h / 2, w, h, size * 0.16);
      context.fill();
      context.stroke();
      // The play button: a ring and its triangle.
      const r = h * 0.24;
      context.strokeStyle = `rgba(${light},0.8)`;
      context.beginPath();
      context.arc(0, 0, r, 0, Math.PI * 2);
      context.stroke();
      context.fillStyle = `rgba(${light},0.95)`;
      context.beginPath();
      context.moveTo(-r * 0.32, -r * 0.45);
      context.lineTo(r * 0.5, 0);
      context.lineTo(-r * 0.32, r * 0.45);
      context.closePath();
      context.fill();
      context.restore();
    };

    const line = (x: number, y: number, size: number, t: Thing) => {
      // The time: a dot; the words: a rounded bar, as long as the line is; the sung ones lit.
      const lit = t.variant > 0.78;
      const length = size * (3.2 + t.variant * 4);
      const thick = Math.max(2, size * 0.32);
      context.fillStyle = `rgba(${light},${lit ? 1 : 0.7})`;
      context.beginPath();
      context.arc(x - length / 2 - size * 0.7, y, thick * 0.62, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = `rgba(${lit ? light : colour},${lit ? 0.95 : 0.75})`;
      context.beginPath();
      context.roundRect(x - length / 2, y - thick / 2, length, thick, thick / 2);
      context.fill();
    };

    const draw = (now: number) => {
      const step = Math.min(64, now - last) / 1000;
      last = now;
      context.clearRect(0, 0, width, height);

      // Where they come from (rising) or go (falling): a soft glow, painted over the whole canvas so
      // the gradient fades out on every side instead of being cut by the box it is drawn in.
      const glowY = rising ? height + 40 : height - 4;
      const haze = context.createRadialGradient(width / 2, glowY, 0, width / 2, glowY, width * 0.5);
      haze.addColorStop(0, `rgba(${colour},0.1)`);
      haze.addColorStop(1, `rgba(${colour},0)`);
      context.fillStyle = haze;
      context.fillRect(0, 0, width, height);

      for (const t of [...things].sort((a, b) => a.depth - b.depth)) {
        const speed = (kind === "note" ? 16 : 10) + t.depth * (kind === "note" ? 44 : 30);
        if (!still) t.y += (rising ? -speed : speed) * step;
        const size = (kind === "note" ? 9 : 10) + t.depth * (kind === "note" ? 18 : 22);
        const x = t.x + Math.sin(now / 2600 + t.phase) * t.sway;
        // Fading in where they come from, out where they go: 140 px each way.
        const fromTop = (t.y + size) / 140;
        const fromBottom = (height - 30 - t.y) / 140;
        const fade = Math.max(0, Math.min(1, fromTop, fromBottom));
        const alpha = (0.08 + t.depth * 0.3) * fade;
        if (alpha > 0.002) {
          context.globalAlpha = alpha;
          context.lineWidth = 1;
          if (kind === "post") card(x, t.y, size, t);
          else if (kind === "note") note(x, t.y, size, t, now);
          else if (kind === "line") line(x, t.y, size, t);
          else clip(x, t.y, size, t);
          context.globalAlpha = 1;
        }
        const gone = rising ? t.y < -80 : t.y > height + 20;
        if (gone) Object.assign(t, thing(width, height, rising, false));
      }
      if (!still) frame = requestAnimationFrame(draw);
    };

    const start = () => {
      cancelAnimationFrame(frame);
      last = performance.now();
      frame = requestAnimationFrame(draw);
    };
    const visibility = () => (document.hidden ? cancelAnimationFrame(frame) : start());

    resize();
    const observer = new ResizeObserver(() => {
      resize();
      if (still) requestAnimationFrame(draw);
    });
    observer.observe(canvas);
    document.addEventListener("visibilitychange", visibility);
    start();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [kind, colour, light]);

  return <canvas ref={canvasRef} aria-hidden className={className} />;
}
