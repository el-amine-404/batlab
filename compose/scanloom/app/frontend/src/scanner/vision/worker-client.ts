import type { DetectionProfile, DetectionResult } from "../types";

export class VisionWorkerClient {
  private worker: Worker | null = null;
  private isReady = false;
  private isProcessing = false;
  private currentRequestId = 0;
  private pendingCallbacks = new Map<number, (res: DetectionResult) => void>();

  constructor() {
    this.init();
  }

  private init() {
    try {
      this.worker = new Worker(new URL("./worker.ts", import.meta.url), {
        type: "module",
      });

      this.worker.onmessage = (event: MessageEvent) => {
        const data = event.data;
        if (!data) return;

        if (data.type === "INIT_RESULT") {
          this.isReady = Boolean(data.success);
        } else if (data.type === "DETECT_RESULT") {
          this.isProcessing = false;
          const resolve = this.pendingCallbacks.get(data.id);
          if (resolve) {
            this.pendingCallbacks.delete(data.id);
            resolve(data.result);
          }
        }
      };

      this.worker.onerror = (err) => {
        console.error("[VisionWorkerClient] Worker error:", err);
        this.isProcessing = false;
      };

      // Request initialization
      this.worker.postMessage({ type: "INIT" });
    } catch (err) {
      console.error("[VisionWorkerClient] Worker initialization failed:", err);
    }
  }

  /**
   * Submits a video or canvas frame for AI document detection.
   * If the worker is still busy with the previous frame, safely skips this frame to prevent queue buildup.
   */
  public async detectFrame(
    source: HTMLVideoElement | HTMLCanvasElement,
    profile: DetectionProfile = "auto"
  ): Promise<DetectionResult | null> {
    if (!this.worker || this.isProcessing) {
      return null;
    }

    try {
      // Capture frame as lightweight ImageBitmap
      const bitmap = await createImageBitmap(source);
      this.isProcessing = true;
      const reqId = ++this.currentRequestId;

      return new Promise<DetectionResult>((resolve) => {
        this.pendingCallbacks.set(reqId, resolve);
        // Transfer ImageBitmap with zero-copy ownership transfer
        this.worker!.postMessage(
          {
            type: "DETECT",
            id: reqId,
            bitmap,
            profile,
          },
          [bitmap]
        );
      });
    } catch (err) {
      this.isProcessing = false;
      return null;
    }
  }

  public terminate(): void {
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    this.pendingCallbacks.clear();
    this.isProcessing = false;
    this.isReady = false;
  }
}
