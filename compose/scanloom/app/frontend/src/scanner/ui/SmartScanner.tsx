import React, { useEffect, useRef, useState } from "react";
import { Camera, CameraOff, LoaderCircle, X } from "lucide-react";
import { Dialog } from "../../ui";
import { useCamera } from "../camera/useCamera";
import { VisionWorkerClient } from "../vision/worker-client";
import { TemporalTracker } from "../vision/tracker";
import { ScannerOverlay } from "./ScannerOverlay";
import { QualityBanner } from "./QualityBanner";
import { ScannerControls } from "./ScannerControls";
import { AdjustCropModal } from "./AdjustCropModal";
import type { CaptureMode, DetectionProfile, Quad, QualityMetrics, ScannerStatus } from "../types";

export interface SmartScannerProps {
  open: boolean;
  onClose: () => void;
  onCapture: (file: File) => void;
  defaultProfile?: DetectionProfile;
  defaultCaptureMode?: CaptureMode;
}

export const SmartScanner: React.FC<SmartScannerProps> = ({
  open,
  onClose,
  onCapture,
  defaultProfile = "auto",
  defaultCaptureMode = "auto",
}) => {
  const {
    videoRef,
    devices,
    activeDeviceId,
    hasTorch,
    torchActive,
    isReady: cameraReady,
    error: cameraError,
    aspectRatio,
    toggleTorch,
    switchCamera,
    captureFrame,
    onLoadedMetadata,
  } = useCamera(open);

  const [captureMode, setCaptureMode] = useState<CaptureMode>(defaultCaptureMode);
  const [profile, setProfile] = useState<DetectionProfile>(defaultProfile);
  const [status, setStatus] = useState<ScannerStatus>("idle");
  const [quad, setQuad] = useState<Quad | null>(null);
  const [quality, setQuality] = useState<QualityMetrics | undefined>(undefined);
  const [isStable, setIsStable] = useState(false);
  const [autoProgress, setAutoProgress] = useState(0);

  // Post-capture crop adjustment state
  const [capturedCanvas, setCapturedCanvas] = useState<HTMLCanvasElement | null>(null);
  const [capturedQuad, setCapturedQuad] = useState<Quad | null>(null);

  const workerClientRef = useRef<VisionWorkerClient | null>(null);
  const trackerRef = useRef<TemporalTracker>(new TemporalTracker());
  const countdownTimerRef = useRef<number | null>(null);
  const countdownStartRef = useRef<number | null>(null);

  // Initialize/terminate Web Worker with modal lifecycle
  useEffect(() => {
    if (!open) {
      if (workerClientRef.current) {
        workerClientRef.current.terminate();
        workerClientRef.current = null;
      }
      trackerRef.current.reset();
      setQuad(null);
      setQuality(undefined);
      setIsStable(false);
      setAutoProgress(0);
      setCapturedCanvas(null);
      setCapturedQuad(null);
      setStatus("idle");
      return;
    }

    workerClientRef.current = new VisionWorkerClient();
    trackerRef.current.reset();
    setStatus("searching");

    return () => {
      if (workerClientRef.current) {
        workerClientRef.current.terminate();
        workerClientRef.current = null;
      }
    };
  }, [open]);

  // Frame detection loop
  useEffect(() => {
    if (!open || !cameraReady || capturedCanvas) return;

    let active = true;

    const runVisionPass = async () => {
      if (!active || !videoRef.current || !workerClientRef.current) return;

      const result = await workerClientRef.current.detectFrame(videoRef.current, profile);
      if (!active || !result) return;

      const trackerState = trackerRef.current.update(result.detected ? result.quad : null);
      const smoothed = trackerState.smoothedQuad;

      setQuad(smoothed);
      setIsStable(trackerState.isStable);

      if (result.quality) {
        setQuality({
          ...result.quality,
          isStable: trackerState.isStable,
        });
      }

      // State transitions
      if (!smoothed) {
        setStatus("searching");
        cancelAutoCountdown();
      } else if (!trackerState.isStable) {
        setStatus("tracking");
        cancelAutoCountdown();
      } else {
        // Document is tracked and stable
        if (captureMode === "auto" && result.quality?.overallQuality !== "poor") {
          startAutoCountdown();
        } else {
          setStatus("ready");
        }
      }
    };

    const interval = window.setInterval(runVisionPass, 95); // ~10.5 FPS AI inference
    return () => {
      active = false;
      window.clearInterval(interval);
      cancelAutoCountdown();
    };
  }, [open, cameraReady, profile, captureMode, capturedCanvas]);

  const cancelAutoCountdown = () => {
    if (countdownTimerRef.current !== null) {
      window.cancelAnimationFrame(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    countdownStartRef.current = null;
    setAutoProgress(0);
  };

  const startAutoCountdown = () => {
    if (status === "auto_countdown" || countdownStartRef.current !== null) return;

    setStatus("auto_countdown");
    const durationMs = 850; // Stable hold duration before shutter fires
    const startTime = performance.now();
    countdownStartRef.current = startTime;

    const tick = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(1, elapsed / durationMs);
      setAutoProgress(progress);

      if (progress >= 1) {
        countdownTimerRef.current = null;
        countdownStartRef.current = null;
        setAutoProgress(0);
        executeCapture();
      } else {
        countdownTimerRef.current = window.requestAnimationFrame(tick);
      }
    };

    countdownTimerRef.current = window.requestAnimationFrame(tick);
  };

  const executeCapture = () => {
    cancelAutoCountdown();
    setStatus("capturing");

    const canvas = captureFrame();
    if (!canvas) {
      setStatus("searching");
      return;
    }

    // Default quadrilateral if capture occurred without clean detection
    const fallbackQuad: Quad = [
      [0.08, 0.08],
      [0.92, 0.08],
      [0.92, 0.92],
      [0.08, 0.92],
    ];

    const finalQuad = quad ?? fallbackQuad;
    setCapturedCanvas(canvas);
    setCapturedQuad(finalQuad);
    setStatus("adjust_crop");
  };

  const handleRetake = () => {
    setCapturedCanvas(null);
    setCapturedQuad(null);
    trackerRef.current.reset();
    setQuad(null);
    setIsStable(false);
    setAutoProgress(0);
    setStatus("searching");
  };

  const handleConfirmCrop = (file: File) => {
    onCapture(file);
    onClose();
  };

  const handleSwitchCamera = () => {
    if (devices.length <= 1) return;
    const currentIdx = devices.findIndex((d) => d.deviceId === activeDeviceId);
    const nextIdx = (currentIdx + 1) % devices.length;
    switchCamera(devices[nextIdx].deviceId);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => !value && onClose()}
      title={capturedCanvas ? "Review Scan" : "Smart Document Scanner"}
      className="camera-dialog"
    >
      {cameraError ? (
        <div className="camera-error">
          <CameraOff size={44} />
          <strong>Live camera unavailable</strong>
          <p>{cameraError}</p>
          <label className="button button-primary">
            <Camera size={18} /> Take photo with system camera
            <input
              hidden
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  onCapture(file);
                  onClose();
                }
              }}
            />
          </label>
          <a
            className="button button-secondary"
            href="http://scanloom.homelab.lan/ca.crt"
            download
          >
            Download local CA certificate
          </a>
          <small>
            On iOS Safari, install the profile, then trust it under Settings → General → About → Certificate Trust Settings.
          </small>
        </div>
      ) : capturedCanvas && capturedQuad ? (
        <AdjustCropModal
          capturedCanvas={capturedCanvas}
          initialQuad={capturedQuad}
          defaultPreset="faithful"
          onConfirm={handleConfirmCrop}
          onRetake={handleRetake}
        />
      ) : (
        <div className="camera-view">
          <div className="camera-feed-container" style={{ aspectRatio }}>
            <video
              ref={videoRef}
              playsInline
              muted
              onLoadedMetadata={onLoadedMetadata}
            />

            {/* Live 60 FPS SVG detection overlay */}
            <ScannerOverlay
              quad={quad}
              status={status}
              isStable={isStable}
              aspectRatio={aspectRatio}
            />

            {/* Real-time Quality / Status Pill */}
            <QualityBanner
              status={status}
              quality={quality}
              isStable={isStable}
              countdownSeconds={1}
            />

            {!cameraReady && (
              <div className="camera-loading-overlay">
                <LoaderCircle className="spin camera-loading" size={36} />
                <span>Starting camera & AI detector...</span>
              </div>
            )}
          </div>

          <ScannerControls
            captureMode={captureMode}
            onToggleCaptureMode={(m) => {
              setCaptureMode(m);
              cancelAutoCountdown();
            }}
            profile={profile}
            onChangeProfile={setProfile}
            hasTorch={hasTorch}
            torchActive={torchActive}
            onToggleTorch={toggleTorch}
            hasMultipleCameras={devices.length > 1}
            onSwitchCamera={handleSwitchCamera}
            status={status}
            isReady={cameraReady}
            capturing={status === "capturing"}
            autoProgress={autoProgress}
            onManualCapture={executeCapture}
          />
        </div>
      )}
    </Dialog>
  );
};
