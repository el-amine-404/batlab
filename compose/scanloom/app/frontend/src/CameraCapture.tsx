import { useEffect, useRef, useState } from "react";
import { Camera, CameraOff, CheckCircle2, LoaderCircle, ScanSearch } from "lucide-react";
import { Button, Dialog } from "./ui";
import { detectDocument, type Quad } from "./detector";

export function CameraCapture({ open, onClose, onCapture }: { open: boolean; onClose: () => void; onCapture: (file: File) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previousQuad = useRef<Quad | null>(null);
  const missCount = useRef(0);

  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [detected, setDetected] = useState(false);
  const [quad, setQuad] = useState<Quad | null>(null);
  const [aspectRatio, setAspectRatio] = useState<string>("4/3");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setError("");
    setReady(false);
    setDetected(false);
    setQuad(null);
    previousQuad.current = null;
    missCount.current = 0;

    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError("Camera access needs trusted HTTPS. Install the Scanloom local CA, then open the HTTPS address.");
      return;
    }

    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1440 } },
      audio: false
    }).then((media) => {
      if (!active) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      if (video.current) {
        video.current.srcObject = media;
        video.current.play().catch(() => {});
      }
    }).catch((reason) => {
      setError(reason?.name === "NotAllowedError"
        ? "Camera permission was denied. Allow camera access in your browser and try again."
        : "No usable camera was found on this device.");
    });

    return () => {
      active = false;
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
    };
  }, [open]);

  useEffect(() => {
    if (!ready) return;
    if (!canvasRef.current) {
      canvasRef.current = document.createElement("canvas");
    }
    const canvas = canvasRef.current;

    const detect = () => {
      const source = video.current;
      if (!source || !source.videoWidth || !source.videoHeight) return;

      const result = detectDocument(source, canvas, 160);
      if (result.detected && result.quad) {
        missCount.current = 0;
        let smoothed: Quad = result.quad;
        const prev = previousQuad.current;
        if (prev) {
          // Exponential moving average for jitter-free tracking
          smoothed = [
            [prev[0][0] * 0.65 + result.quad[0][0] * 0.35, prev[0][1] * 0.65 + result.quad[0][1] * 0.35],
            [prev[1][0] * 0.65 + result.quad[1][0] * 0.35, prev[1][1] * 0.65 + result.quad[1][1] * 0.35],
            [prev[2][0] * 0.65 + result.quad[2][0] * 0.35, prev[2][1] * 0.65 + result.quad[2][1] * 0.35],
            [prev[3][0] * 0.65 + result.quad[3][0] * 0.35, prev[3][1] * 0.65 + result.quad[3][1] * 0.35],
          ];
        }
        previousQuad.current = smoothed;
        setQuad(smoothed);
        setDetected(true);
      } else {
        missCount.current++;
        if (missCount.current >= 2) {
          previousQuad.current = null;
          setQuad(null);
          setDetected(false);
        }
      }
    };

    detect();
    const timer = window.setInterval(detect, 200);
    return () => window.clearInterval(timer);
  }, [ready]);

  const onLoadedMetadata = () => {
    if (video.current?.videoWidth && video.current?.videoHeight) {
      setAspectRatio(`${video.current.videoWidth} / ${video.current.videoHeight}`);
      setReady(true);
    }
  };

  const capture = () => {
    const source = video.current;
    if (!source || !source.videoWidth) return;
    setCapturing(true);
    const canvas = document.createElement("canvas");
    canvas.width = source.videoWidth;
    canvas.height = source.videoHeight;
    canvas.getContext("2d")!.drawImage(source, 0, 0);
    canvas.toBlob((blob) => {
      setCapturing(false);
      if (!blob) return;
      onCapture(new File([blob], `scanloom-camera-${new Date().toISOString().replace(/[:.]/g, "-")}.jpg`, { type: "image/jpeg" }));
      onClose();
    }, "image/jpeg", 0.97);
  };

  const pointsString = quad
    ? quad.map(([x, y]) => `${(x * 100).toFixed(2)},${(y * 100).toFixed(2)}`).join(" ")
    : "";

  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()} title="Take a document photo" className="camera-dialog">
      {error ? (
        <div className="camera-error">
          <CameraOff />
          <strong>Live camera unavailable</strong>
          <p>{error}</p>
          <label className="button button-primary">
            <Camera /> Take photo with device
            <input hidden type="file" accept="image/*" capture="environment" onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) { onCapture(file); onClose(); }
            }} />
          </label>
          <a className="button button-secondary" href="http://scanloom.homelab.lan/ca.crt" download>Download local CA certificate</a>
          <small>On iPhone, install the profile, then enable it under Settings → General → About → Certificate Trust Settings.</small>
        </div>
      ) : (
        <div className="camera-view">
          <div className="camera-feed-container" style={{ aspectRatio }}>
            <video ref={video} playsInline muted onLoadedMetadata={onLoadedMetadata} />
            <svg className="camera-guide-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              {detected && quad ? (
                <>
                  <polygon points={pointsString} className="camera-guide-poly active" />
                  {quad.map(([x, y], idx) => (
                    <circle key={idx} cx={(x * 100).toFixed(2)} cy={(y * 100).toFixed(2)} r="2" className="camera-guide-corner" />
                  ))}
                </>
              ) : (
                <rect x="12" y="10" width="76" height="80" rx="4" className="camera-guide-poly idle" />
              )}
            </svg>
            <div className={`camera-status-pill ${detected ? "is-detected" : "is-searching"}`}>
              {detected ? (
                <>
                  <CheckCircle2 size={14} />
                  <span>Document detected</span>
                </>
              ) : (
                <>
                  <ScanSearch size={14} />
                  <span>Align document in camera view</span>
                </>
              )}
            </div>
            {!ready && <LoaderCircle className="spin camera-loading" />}
          </div>
        </div>
      )}
      <div className="camera-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        {!error && (
          <Button onClick={capture} disabled={!ready || capturing} variant={detected ? "primary" : "secondary"}>
            {capturing ? <LoaderCircle className="spin" /> : <Camera />} {detected ? "Capture page" : "Capture photo"}
          </Button>
        )}
      </div>
    </Dialog>
  );
}
