import React from "react";
import { Camera, FlipHorizontal, Sparkles, Zap, ZapOff } from "lucide-react";
import type { CaptureMode, DetectionProfile, ScannerStatus } from "../types";

interface ScannerControlsProps {
  captureMode: CaptureMode;
  onToggleCaptureMode: (mode: CaptureMode) => void;
  profile: DetectionProfile;
  onChangeProfile: (profile: DetectionProfile) => void;
  hasTorch: boolean;
  torchActive: boolean;
  onToggleTorch: () => void;
  hasMultipleCameras: boolean;
  onSwitchCamera: () => void;
  status: ScannerStatus;
  isReady: boolean;
  capturing: boolean;
  autoProgress: number; // 0 to 1 for radial countdown progress
  onManualCapture: () => void;
}

export const ScannerControls: React.FC<ScannerControlsProps> = ({
  captureMode,
  onToggleCaptureMode,
  profile,
  onChangeProfile,
  hasTorch,
  torchActive,
  onToggleTorch,
  hasMultipleCameras,
  onSwitchCamera,
  status,
  isReady,
  capturing,
  autoProgress,
  onManualCapture,
}) => {
  // 60px circle circumference: 2 * PI * 26 ≈ 163.36
  const radius = 27;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference * (1 - Math.max(0, Math.min(1, autoProgress)));

  return (
    <div className="scanner-bottom-controls">
      {/* Top row: Profile & Mode switches */}
      <div className="scanner-modes-row">
        {/* Profile segmented pill */}
        <div className="scanner-pill-group" role="radiogroup" aria-label="Detection profile">
          <button
            type="button"
            className={`scanner-tab-btn ${profile === "auto" ? "active" : ""}`}
            onClick={() => onChangeProfile("auto")}
          >
            Auto
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${profile === "card" ? "active" : ""}`}
            onClick={() => onChangeProfile("card")}
          >
            Card
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${profile === "document" ? "active" : ""}`}
            onClick={() => onChangeProfile("document")}
          >
            Document
          </button>
        </div>

        {/* Mode pill: Auto vs Manual */}
        <div className="scanner-pill-group" role="radiogroup" aria-label="Capture mode">
          <button
            type="button"
            className={`scanner-tab-btn ${captureMode === "auto" ? "active" : ""}`}
            onClick={() => onToggleCaptureMode("auto")}
          >
            <Sparkles size={12} /> Auto
          </button>
          <button
            type="button"
            className={`scanner-tab-btn ${captureMode === "manual" ? "active" : ""}`}
            onClick={() => onToggleCaptureMode("manual")}
          >
            Manual
          </button>
        </div>
      </div>

      {/* Main Shutter row */}
      <div className="scanner-shutter-row">
        {/* Left utility: Torch */}
        <div className="scanner-shutter-side">
          {hasTorch && (
            <button
              type="button"
              className={`scanner-icon-btn ${torchActive ? "active" : ""}`}
              onClick={onToggleTorch}
              aria-label={torchActive ? "Turn off torch" : "Turn on torch"}
            >
              {torchActive ? <Zap size={20} /> : <ZapOff size={20} />}
            </button>
          )}
        </div>

        {/* Center: Large Shutter Button */}
        <div className="scanner-shutter-center">
          <button
            type="button"
            className={`scanner-shutter-btn ${status === "auto_countdown" ? "is-counting" : ""} ${capturing ? "is-capturing" : ""}`}
            onClick={onManualCapture}
            disabled={!isReady || capturing}
            aria-label="Capture document"
          >
            {/* Countdown SVG progress ring */}
            {captureMode === "auto" && autoProgress > 0 && (
              <svg className="scanner-shutter-svg" viewBox="0 0 64 64">
                <circle
                  cx="32"
                  cy="32"
                  r={radius}
                  className="scanner-shutter-track"
                />
                <circle
                  cx="32"
                  cy="32"
                  r={radius}
                  className="scanner-shutter-meter"
                  style={{
                    strokeDasharray: circumference,
                    strokeDashoffset,
                  }}
                />
              </svg>
            )}
            <div className="scanner-shutter-inner">
              <Camera size={26} />
            </div>
          </button>
        </div>

        {/* Right utility: Switch Camera */}
        <div className="scanner-shutter-side">
          {hasMultipleCameras && (
            <button
              type="button"
              className="scanner-icon-btn"
              onClick={onSwitchCamera}
              aria-label="Switch camera"
            >
              <FlipHorizontal size={20} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
