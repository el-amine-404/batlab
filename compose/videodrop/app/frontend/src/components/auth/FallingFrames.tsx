import { useEffect, useRef } from "react";

/** Tiles at once; each is drawn by its depth (0 far, 1 near): size, speed, light. */
const COUNT = 22;

type Tile = { x: number; y: number; depth: number; sway: number; phase: number; play: boolean };

function tile(width: number, height: number, anywhere: boolean): Tile {
  return {
    x: Math.random() * width,
    y: anywhere ? Math.random() * height : -40 - Math.random() * height * 0.3,
    depth: Math.random(),
    sway: 6 + Math.random() * 14,
    phase: Math.random() * Math.PI * 2,
    play: Math.random() < 0.45,
  };
}

/**
 * The logo's idea, slowly: small blank video frames (some with a play
 * sign) fall at different depths and fade into a glowing tray at the
 * bottom. A canvas, about 60 drawings a second while the tab is seen;
 * paused when it isn't, one still drawing for whoever asks for less motion.
 */
export function FallingFrames({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let tiles: Tile[] = [];
    let frame = 0;
    let last = performance.now();

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (!tiles.length) tiles = Array.from({ length: COUNT }, () => tile(width, height, true));
    };

    const draw = (now: number) => {
      const step = Math.min(64, now - last) / 1000;
      last = now;
      context.clearRect(0, 0, width, height);
      const tray = height - 36;

      // The tray: a glowing line the frames land in.
      const glow = context.createLinearGradient(0, 0, width, 0);
      glow.addColorStop(0, "rgba(56,189,248,0)");
      glow.addColorStop(0.5, "rgba(34,211,238,0.55)");
      glow.addColorStop(1, "rgba(56,189,248,0)");
      context.fillStyle = glow;
      context.fillRect(width * 0.1, tray, width * 0.8, 2);
      const haze = context.createRadialGradient(width / 2, tray, 0, width / 2, tray, width * 0.45);
      haze.addColorStop(0, "rgba(34,211,238,0.12)");
      haze.addColorStop(1, "rgba(34,211,238,0)");
      context.fillStyle = haze;
      context.fillRect(0, tray - width * 0.2, width, width * 0.4);

      for (const t of [...tiles].sort((a, b) => a.depth - b.depth)) {
        if (!still) t.y += (14 + t.depth * 46) * step;
        const size = 10 + t.depth * 26;
        const x = t.x + Math.sin(now / 2600 + t.phase) * t.sway;
        // Fading in at the top, out into the tray.
        const fade = Math.min(1, (t.y + size) / 120) * Math.max(0, Math.min(1, (tray - t.y) / 140));
        const alpha = (0.08 + t.depth * 0.3) * fade;
        if (alpha > 0.002) {
          const w = size * 1.6;
          context.globalAlpha = alpha;
          context.fillStyle = "rgba(125,211,252,0.18)";
          context.strokeStyle = "rgba(125,211,252,0.9)";
          context.lineWidth = 1;
          context.beginPath();
          context.roundRect(x - w / 2, t.y - size / 2, w, size, size * 0.22);
          context.fill();
          context.stroke();
          if (t.play) {
            const p = size * 0.22;
            context.fillStyle = "rgba(186,230,253,0.95)";
            context.beginPath();
            context.moveTo(x - p * 0.7, t.y - p);
            context.lineTo(x + p, t.y);
            context.lineTo(x - p * 0.7, t.y + p);
            context.closePath();
            context.fill();
          }
          context.globalAlpha = 1;
        }
        if (t.y > tray + 10) Object.assign(t, tile(width, height, false));
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
  }, []);

  return <canvas ref={canvasRef} aria-hidden className={className} />;
}
