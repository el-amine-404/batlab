import { useCallback, useEffect, useRef, useState } from "react";
import type { CameraDevice } from "../types";

export interface UseCameraResult {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  stream: MediaStream | null;
  devices: CameraDevice[];
  activeDeviceId: string | null;
  hasTorch: boolean;
  torchActive: boolean;
  isReady: boolean;
  error: string | null;
  aspectRatio: string;
  toggleTorch: () => Promise<void>;
  switchCamera: (deviceId: string) => Promise<void>;
  captureFrame: () => HTMLCanvasElement | null;
  onLoadedMetadata: () => void;
}

export function useCamera(open: boolean): UseCameraResult {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [activeDeviceId, setActiveDeviceId] = useState<string | null>(null);
  const [hasTorch, setHasTorch] = useState(false);
  const [torchActive, setTorchActive] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aspectRatio, setAspectRatio] = useState<string>("4/3");

  const stopActiveStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    setHasTorch(false);
    setTorchActive(false);
    setIsReady(false);
  }, []);

  const enumerateDevices = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.enumerateDevices) return;
      const allDevices = await navigator.mediaDevices.enumerateDevices();
      const videoInputs = allDevices
        .filter((d) => d.kind === "videoinput")
        .map((d, idx) => ({
          deviceId: d.deviceId,
          label: d.label || `Camera ${idx + 1}`,
        }));
      setDevices(videoInputs);
    } catch {
      // Ignore enumeration errors
    }
  }, []);

  const startStream = useCallback(
    async (deviceId?: string) => {
      setError(null);
      setIsReady(false);
      stopActiveStream();

      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setError(
          "Camera access requires HTTPS or localhost. Install the Scanloom local CA and open via HTTPS."
        );
        return;
      }

      const constraints: MediaStreamConstraints = {
        audio: false,
        video: deviceId
          ? {
              deviceId: { exact: deviceId },
              width: { ideal: 2560 },
              height: { ideal: 1440 },
            }
          : {
              facingMode: { ideal: "environment" },
              width: { ideal: 2560 },
              height: { ideal: 1440 },
            },
      };

      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        streamRef.current = stream;

        const videoTrack = stream.getVideoTracks()[0];
        if (videoTrack) {
          const settings = videoTrack.getSettings();
          if (settings.deviceId) {
            setActiveDeviceId(settings.deviceId);
          }

          // Check torch capability
          const capabilities = (videoTrack.getCapabilities?.() as any) || {};
          setHasTorch(Boolean(capabilities.torch));
        }

        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {});
        }

        await enumerateDevices();
      } catch (err: any) {
        console.error("[useCamera] Failed to open camera:", err);
        if (err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError") {
          setError("Camera permission was denied. Allow camera access in browser settings.");
        } else if (err?.name === "NotFoundError" || err?.name === "DevicesNotFoundError") {
          setError("No video camera found on this device.");
        } else {
          // Retry with lower resolution constraints
          try {
            const fallbackStream = await navigator.mediaDevices.getUserMedia({
              video: { facingMode: { ideal: "environment" } },
              audio: false,
            });
            streamRef.current = fallbackStream;
            if (videoRef.current) {
              videoRef.current.srcObject = fallbackStream;
              videoRef.current.play().catch(() => {});
            }
          } catch {
            setError("Unable to initialize camera video feed.");
          }
        }
      }
    },
    [stopActiveStream, enumerateDevices]
  );

  useEffect(() => {
    if (!open) {
      stopActiveStream();
      return;
    }

    startStream(activeDeviceId ?? undefined);

    return () => {
      stopActiveStream();
    };
  }, [open]);

  const onLoadedMetadata = useCallback(() => {
    if (videoRef.current?.videoWidth && videoRef.current?.videoHeight) {
      setAspectRatio(`${videoRef.current.videoWidth} / ${videoRef.current.videoHeight}`);
      setIsReady(true);
    }
  }, []);

  const switchCamera = useCallback(
    async (deviceId: string) => {
      setActiveDeviceId(deviceId);
      await startStream(deviceId);
    },
    [startStream]
  );

  const toggleTorch = useCallback(async () => {
    if (!streamRef.current || !hasTorch) return;
    const track = streamRef.current.getVideoTracks()[0];
    if (!track) return;

    try {
      const nextState = !torchActive;
      await (track as any).applyConstraints({
        advanced: [{ torch: nextState }],
      });
      setTorchActive(nextState);
    } catch (err) {
      console.warn("[useCamera] Torch control failed:", err);
    }
  }, [hasTorch, torchActive]);

  const captureFrame = useCallback((): HTMLCanvasElement | null => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) return null;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas;
  }, []);

  return {
    videoRef,
    stream: streamRef.current,
    devices,
    activeDeviceId,
    hasTorch,
    torchActive,
    isReady,
    error,
    aspectRatio,
    toggleTorch,
    switchCamera,
    captureFrame,
    onLoadedMetadata,
  };
}
