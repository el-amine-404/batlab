import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, RefreshCw, Wand2 } from "lucide-react";
import type { Point, Quad } from "../types";
import { applyEnhancement, type EnhancementPreset } from "../processing/enhancer";
import { warpPerspective } from "../processing/perspective";

interface AdjustCropModalProps {
  capturedCanvas: HTMLCanvasElement;
  initialQuad: Quad;
  defaultPreset?: EnhancementPreset;
  onConfirm: (file: File) => void;
  onRetake: () => void;
}

export const AdjustCropModal: React.FC<AdjustCropModalProps> = ({
  capturedCanvas,
  initialQuad,
  defaultPreset = "faithful",
  onConfirm,
  onRetake,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [quad, setQuad] = useState<Quad>(initialQuad);
  const [draggingIdx, setDraggingIdx] = useState<number | null>(null);
  const [preset, setPreset] = useState<EnhancementPreset>(defaultPreset);
  const [previewDataUrl, setPreviewDataUrl] = useState<string>("");
  const [isProcessing, setIsProcessing] = useState(false);

  // Convert captured canvas to data URL for display
  useEffect(() => {
    setPreviewDataUrl(capturedCanvas.toDataURL("image/jpeg", 0.95));
  }, [capturedCanvas]);

  const handlePointerDown = (idx: number, e: React.PointerEvent) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDraggingIdx(idx);
  };

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (draggingIdx === null || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

      setQuad((prev) => {
        const next: Quad = [...prev];
        next[draggingIdx] = [x, y];
        return next;
      });
    },
    [draggingIdx]
  );

  const handlePointerUp = (e: React.PointerEvent) => {
    if (draggingIdx !== null) {
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {}
      setDraggingIdx(null);
    }
  };

  const handleConfirm = () => {
    setIsProcessing(true);

    // Warp perspective with user-adjusted corners
    const warped = warpPerspective(capturedCanvas, quad);

    // Apply chosen enhancement preset
    const enhanced = applyEnhancement(warped, preset);

    // Export as high quality JPEG File
    enhanced.toBlob(
      (blob) => {
        setIsProcessing(false);
        if (!blob) return;
        const filename = `scanloom-scan-${new Date().toISOString().replace(/[:.]/g, "-")}.jpg`;
        const file = new File([blob], filename, { type: "image/jpeg" });
        onConfirm(file);
      },
      "image/jpeg",
      0.97
    );
  };

  const pointsString = quad
    .map(([x, y]) => `${(x * 100).toFixed(2)},${(y * 100).toFixed(2)}`)
    .join(" ");

  return (
    <div className="scanner-crop-modal">
      <div className="scanner-crop-header">
        <strong>Adjust & Review Crop</strong>
        <span>Drag any corner to fine-tune the boundaries</span>
      </div>

      <div
        ref={containerRef}
        className="scanner-crop-preview"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        {previewDataUrl && (
          <img
            src={previewDataUrl}
            alt="Captured scan preview"
            className="scanner-crop-img"
            draggable={false}
          />
        )}

        <svg
          className="scanner-crop-svg"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
        >
          <polygon points={pointsString} className="scanner-crop-poly" />
          {quad.map(([x, y], idx) => (
            <g
              key={idx}
              transform={`translate(${(x * 100).toFixed(2)}, ${(y * 100).toFixed(2)})`}
              onPointerDown={(e) => handlePointerDown(idx, e)}
              className="scanner-crop-handle-group"
            >
              {/* Outer touch target */}
              <circle r="7" className="scanner-crop-touch-target" />
              {/* Visual pin */}
              <circle
                r="3.5"
                className={`scanner-crop-handle ${draggingIdx === idx ? "active" : ""}`}
              />
              <circle r="1.5" className="scanner-crop-center-dot" />
            </g>
          ))}
        </svg>
      </div>

      {/* Enhancement Presets row */}
      <div className="scanner-preset-row">
        <span className="scanner-preset-label">
          <Wand2 size={14} /> Color preset:
        </span>
        <div className="scanner-pill-group">
          <button
            type="button"
            className={`scanner-tab-btn ${preset === "faithful" ? "active" : ""}`}
            onClick={() => setPreset("faithful")}
          >
            Faithful
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${preset === "paper" ? "active" : ""}`}
            onClick={() => setPreset("paper")}
          >
            Clean Paper
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${preset === "mono" ? "active" : ""}`}
            onClick={() => setPreset("mono")}
          >
            B&W
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${preset === "original" ? "active" : ""}`}
            onClick={() => setPreset("original")}
          >
            Original
          </button>
        </div>
      </div>

      {/* Action Footer */}
      <div className="scanner-crop-actions">
        <button
          type="button"
          className="button button-secondary"
          onClick={onRetake}
          disabled={isProcessing}
        >
          <RefreshCw size={16} /> Retake
        </button>
        <button
          type="button"
          className="button button-primary"
          onClick={handleConfirm}
          disabled={isProcessing}
        >
          <Check size={16} /> {isProcessing ? "Processing..." : "Keep Page"}
        </button>
      </div>
    </div>
  );
};
