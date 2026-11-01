import { Camera, CameraOff, Check, Flashlight, FlashlightOff, Hand, SwitchCamera, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { AutoShutter, Tracker } from "./tracker";
import type { DetectionResult, Point, Quad } from "./types";

const INPUT = 256;
const MIN_FRAME_MS = 80;

type Props = {
  /** A picture was taken. hint: where the page is in it, when the picture is the frame that was analysed. */
  onCapture: (file: File, hint?: Point[]) => void;
  onDone: (count: number) => void;
};

async function takeFullPhoto(track: MediaStreamTrack): Promise<Blob | null> {
  const ImageCaptureClass = (window as unknown as { ImageCapture?: new (track: MediaStreamTrack) => { takePhoto(): Promise<Blob> } }).ImageCapture;
  if (!ImageCaptureClass) return null;
  try {
    return await Promise.race([
      new ImageCaptureClass(track).takePhoto(),
      new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 2500)),
    ]);
  } catch {
    return null;
  }
}

function problemMessage(error: unknown): string {
  const name = (error as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "The browser isn't allowed to use the camera. Allow it in the site's settings, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "This device has no camera the browser can use.";
  if (name === "NotReadableError") return "Another app is using the camera. Close it, then try again.";
  return "The camera couldn't start.";
}

/**
 * The phone as a scanner, on the whole screen: the picture, the page's outline
 * following it, a shutter that presses itself when the page holds still, and the
 * pages going to the scan as they are taken, so another can follow straight away.
 * The drawing and the detection never wait for each other, and nothing here
 * re-renders per frame: only words and buttons are React's.
 */
export function Scanner({ onCapture, onDone }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const shot = useRef<HTMLCanvasElement | null>(null);
  const target = useRef<{ quad: Quad | null; progress: number; good: boolean }>({ quad: null, progress: 0, good: false });
  const shutter = useRef(new AutoShutter());
  const autoRef = useRef(true);
  const busyTaking = useRef(false);
  const takeRef = useRef<(by: "auto" | "hand") => void>(() => {});

  const [ready, setReady] = useState(false);
  const [detector, setDetector] = useState<"loading" | "ready" | "failed">("loading");
  const [problem, setProblem] = useState<string | null>(null);
  const [message, setMessage] = useState("Point at a document");
  const [auto, setAuto] = useState(true);
  const [torch, setTorch] = useState<boolean | null>(null);
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [flash, setFlash] = useState(0);
  const [taken, setTaken] = useState<string[]>([]);
  const area = useRef<HTMLDivElement>(null);

  const stop = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  const start = useCallback(async (deviceId?: string) => {
    setProblem(null);
    setReady(false);
    stop();
    if (!navigator.mediaDevices?.getUserMedia) {
      setProblem("This page needs HTTPS for the live camera. Open it with https:// and trust the home CA (http://scanloom.homelab.lan/ca.crt).");
      return;
    }
    const size = { width: { ideal: 2560 }, height: { ideal: 1920 } };
    try {
      const opened = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: deviceId ? { deviceId: { exact: deviceId }, ...size } : { facingMode: { ideal: "environment" }, ...size },
      });
      stream.current = opened;
      const element = video.current!;
      element.srcObject = opened;
      await element.play().catch(() => {});
      const [track] = opened.getVideoTracks();
      setTorch((track.getCapabilities?.() as { torch?: boolean })?.torch ? false : null);
      setCameras((await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput"));
    } catch (error) {
      setProblem(problemMessage(error));
    }
  }, [stop]);

  useEffect(() => {
    void start();
    return stop;
  }, [start, stop]);

  // The picture's box: as large as fits, in the camera's own shape.
  useEffect(() => {
    const element = video.current, parent = area.current;
    if (!element || !parent) return;
    const fit = () => {
      if (!element.videoWidth) return;
      const scale = Math.min(parent.clientWidth / element.videoWidth, parent.clientHeight / element.videoHeight);
      setBox({ width: Math.round(element.videoWidth * scale), height: Math.round(element.videoHeight * scale) });
      setReady(true);
    };
    const observer = new ResizeObserver(fit);
    observer.observe(parent);
    element.addEventListener("loadedmetadata", fit);
    element.addEventListener("resize", fit);
    return () => {
      observer.disconnect();
      element.removeEventListener("loadedmetadata", fit);
      element.removeEventListener("resize", fit);
    };
  }, []);

  const take = useCallback(async (by: "auto" | "hand") => {
    const element = video.current;
    const [track] = stream.current?.getVideoTracks() ?? [];
    if (!element?.videoWidth || !track || busyTaking.current) return;
    busyTaking.current = true;
    setFlash((value) => value + 1);
    navigator.vibrate?.(30);
    const quad = target.current.quad;
    if (by === "hand") shutter.current.note(quad);
    // The same instant for the thumbnail, whatever the full photo takes.
    const small = document.createElement("canvas");
    small.width = 120;
    small.height = Math.round(120 * element.videoHeight / element.videoWidth);
    small.getContext("2d")!.drawImage(element, 0, 0, small.width, small.height);
    setTaken((list) => [...list, small.toDataURL("image/jpeg", 0.6)]);
    const name = `scan-${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14)}`;
    try {
      const photo = await takeFullPhoto(track);
      if (photo) {
        onCapture(new File([photo], `${name}.jpg`, { type: photo.type || "image/jpeg" }));
      } else {
        const frame = (shot.current ??= document.createElement("canvas"));
        frame.width = element.videoWidth;
        frame.height = element.videoHeight;
        frame.getContext("2d")!.drawImage(element, 0, 0);
        const blob = await new Promise<Blob | null>((resolve) => frame.toBlob(resolve, "image/jpeg", 0.95));
        if (blob) onCapture(new File([blob], `${name}.jpg`, { type: "image/jpeg" }), quad ?? undefined);
      }
    } finally {
      busyTaking.current = false;
    }
  }, [onCapture]);
  takeRef.current = (by) => void take(by);
  autoRef.current = auto;

  // Detection: the next frame only when the last has been answered, so a slow phone never queues up.
  useEffect(() => {
    const worker = new Worker(new URL("./vision/worker.ts", import.meta.url), { type: "module" });
    const small = document.createElement("canvas");
    small.width = small.height = INPUT;
    const context = small.getContext("2d", { willReadFrequently: true })!;
    const tracker = new Tracker();
    let stopped = false, timer = 0, waiting = false, sent = 0, lastMessage = "";
    const say = (text: string) => { if (text !== lastMessage) { lastMessage = text; setMessage(text); } };

    const send = () => {
      if (stopped || waiting) return;
      const element = video.current;
      if (document.hidden || !element?.videoWidth || element.readyState < 2) { timer = window.setTimeout(send, 200); return; }
      context.drawImage(element, 0, 0, INPUT, INPUT);
      const { data } = context.getImageData(0, 0, INPUT, INPUT);
      waiting = true;
      sent = performance.now();
      worker.postMessage({ type: "detect", id: 0, pixels: data, tracking: Boolean(target.current.quad) }, [data.buffer]);
    };

    const handle = (result: DetectionResult) => {
      const now = performance.now();
      const tracked = tracker.update(result.quad, now);
      const quality = result.quality;
      // Only the framing holds the shutter back. White paper looks like glare and a 256-pixel frame
      // can't judge sharpness: those become notes on the finished page, never a camera that won't fire.
      const acceptable = Boolean(tracked.quad) && Boolean(quality?.isGeometryValid && quality.isWithinBounds)
        && (quality?.areaFraction ?? 0) >= 0.07 && (quality?.areaFraction ?? 1) <= 0.94;
      if (area.current) area.current.dataset.detectMs = String(Math.round(result.ms));
      const answer = autoRef.current ? shutter.current.update(tracked, acceptable) : { progress: 0, fire: false, waitingForNewPage: false };
      target.current = { quad: tracked.quad, progress: answer.progress, good: tracked.steadyMs > 250 };
      if (answer.fire) takeRef.current("auto");
      if (!tracked.quad) say(quality && quality.exposureScore < 0.14 ? "Too dark: more light, or the torch" : "Point at a document");
      else if (answer.waitingForNewPage) say("Got it. Put the next page in view");
      else if (!acceptable) say(quality?.warningMessage ?? "Hold the whole page in view");
      else say(autoRef.current ? "Hold still…" : "Ready when you are");
      timer = window.setTimeout(send, Math.max(0, MIN_FRAME_MS - (now - sent)));
    };

    worker.onmessage = ({ data }) => {
      if (data.type === "loaded") { setDetector("ready"); send(); }
      else if (data.type === "result") { waiting = false; handle(data.result); }
      else if (data.type === "failed") {
        console.error("Scanner:", data.message);
        waiting = false;
        setDetector("failed");
      }
    };
    worker.onerror = (event) => { console.error("Scanner:", event.message); setDetector("failed"); };
    worker.postMessage({ type: "load" });
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      worker.terminate();
    };
  }, []);

  // The outline, drawn at the screen's own rate, gliding to where the detector last said.
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d")!;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    element.width = box.width * ratio;
    element.height = box.height * ratio;
    let frame = 0, drawn: number[][] | null = null, fade = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const { quad, progress, good } = target.current;
      const width = element.width, height = element.height;
      context.clearRect(0, 0, width, height);
      if (quad) {
        drawn = drawn ? drawn.map((point, index) => [point[0] + (quad[index][0] - point[0]) * 0.35, point[1] + (quad[index][1] - point[1]) * 0.35]) : quad.map((point) => [...point]);
        fade = Math.min(1, fade + 0.15);
      } else {
        fade = Math.max(0, fade - 0.1);
        if (!fade) drawn = null;
      }
      if (!drawn) return;
      const points = drawn.map(([x, y]) => [x * width, y * height]);
      const outline = () => {
        context.beginPath();
        points.forEach(([x, y], index) => (index ? context.lineTo(x, y) : context.moveTo(x, y)));
        context.closePath();
      };
      context.globalAlpha = fade;
      context.fillStyle = "rgba(0,0,0,0.38)";
      context.beginPath();
      context.rect(0, 0, width, height);
      points.forEach(([x, y], index) => (index ? context.lineTo(x, y) : context.moveTo(x, y)));
      context.closePath();
      context.fill("evenodd");
      context.lineJoin = "round";
      context.lineWidth = 3 * ratio;
      context.strokeStyle = good ? "#5eead4" : "rgba(255,255,255,0.9)";
      outline();
      context.stroke();
      if (progress > 0) {
        const perimeter = points.reduce((sum, [x, y], index) => {
          const [nx, ny] = points[(index + 1) % 4];
          return sum + Math.hypot(nx - x, ny - y);
        }, 0);
        context.lineWidth = 6 * ratio;
        context.strokeStyle = "#2dd4bf";
        context.setLineDash([perimeter * progress, perimeter]);
        outline();
        context.stroke();
        context.setLineDash([]);
      }
      context.globalAlpha = 1;
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [box]);

  const toggleTorch = async () => {
    const [track] = stream.current?.getVideoTracks() ?? [];
    if (!track || torch === null) return;
    try {
      await track.applyConstraints({ advanced: [{ torch: !torch } as MediaTrackConstraintSet] });
      setTorch(!torch);
    } catch {
      setTorch(null);
    }
  };

  const nextCamera = () => {
    const current = stream.current?.getVideoTracks()[0]?.getSettings().deviceId;
    const at = cameras.findIndex((camera) => camera.deviceId === current);
    void start(cameras[(at + 1) % cameras.length].deviceId);
  };

  const round = "flex size-11 items-center justify-center rounded-full bg-white/15 text-white transition-colors hover:bg-white/25 focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none";

  return (
    <div role="dialog" aria-modal="true" aria-label="Scan with the camera" className="fixed inset-0 z-[70] flex flex-col bg-black pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-white">
      <div className="flex shrink-0 items-center justify-between gap-2 p-3">
        <button type="button" className={round} aria-label="Close the camera" onClick={() => onDone(taken.length)}><X className="size-5" /></button>
        <p className="min-w-0 truncate rounded-full bg-white/10 px-4 py-2 text-sm font-medium" role="status" aria-live="polite">
          {problem ? "Camera unavailable" : detector === "loading" ? "Getting the detector ready…" : detector === "failed" ? "Detection is off: use the shutter" : message}
        </p>
        <div className="flex gap-2">
          {torch !== null && (
            <button type="button" className={cn(round, torch && "bg-white text-black hover:bg-white/90")} aria-pressed={torch} aria-label="Torch" onClick={() => void toggleTorch()}>
              {torch ? <FlashlightOff className="size-5" /> : <Flashlight className="size-5" />}
            </button>
          )}
          {cameras.length > 1 && <button type="button" className={round} aria-label="Change camera" onClick={nextCamera}><SwitchCamera className="size-5" /></button>}
        </div>
      </div>

      <div ref={area} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
        <div className="relative" style={{ width: box.width || "100%", height: box.height || "100%" }}>
          <video ref={video} playsInline muted autoPlay className="size-full object-contain" />
          <canvas ref={canvas} className="pointer-events-none absolute inset-0 size-full" />
          {flash > 0 && <div key={flash} aria-hidden className="pointer-events-none absolute inset-0 animate-[shot_.35s_ease-out_forwards] bg-white" />}
        </div>
        {!ready && !problem && <p className="absolute text-sm text-white/70">Starting the camera…</p>}
        {problem && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 p-6 text-center">
            <CameraOff className="size-10 text-white/70" />
            <p className="max-w-sm text-sm">{problem}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={() => void start()}>Try again</Button>
              <Button asChild variant="secondary">
                <label>
                  <Camera /> Use the phone's camera app
                  <input hidden type="file" accept="image/*" capture="environment" onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) { setTaken((list) => [...list, ""]); onCapture(file); }
                    event.target.value = "";
                  }} />
                </label>
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-4">
        <div className="flex min-w-0 items-center gap-2">
          {taken.length > 0 && (
            <div className="relative size-14 shrink-0">
              {taken.slice(-3).map((source, index, shown) => (
                <span key={taken.length - shown.length + index} className="absolute inset-0 overflow-hidden rounded-lg border-2 border-white bg-neutral-700"
                      style={{ transform: `rotate(${(index - shown.length + 1) * 5}deg)` }}>
                  {source && <img src={source} alt="" className="size-full object-cover" />}
                </span>
              ))}
              <span className="absolute -top-2 -right-2 flex size-6 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">{taken.length}</span>
            </div>
          )}
          <button type="button" aria-pressed={auto} onClick={() => setAuto(!auto)}
                  className="flex min-h-11 items-center gap-2 rounded-full bg-white/15 px-3 text-sm font-medium transition-colors hover:bg-white/25 focus-visible:ring-2 focus-visible:ring-white focus-visible:outline-none">
            {auto ? <Wand2 className="size-4" /> : <Hand className="size-4" />} {auto ? "Auto" : "Manual"}
          </button>
        </div>
        <button type="button" aria-label="Take the picture" disabled={!ready || Boolean(problem)} onClick={() => void take("hand")}
                className="flex size-20 items-center justify-center rounded-full border-4 border-white transition-transform active:scale-95 disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-black focus-visible:outline-none">
          <span className="size-[3.75rem] rounded-full bg-white" />
        </button>
        <div className="flex justify-end">
          <Button size="lg" disabled={!taken.length} onClick={() => onDone(taken.length)} className="px-4"><Check /> Done</Button>
        </div>
      </div>
    </div>
  );
}
