import React from "react";
import {
  AlertCircle,
  CheckCircle2,
  Maximize2,
  ScanSearch,
  Sparkles,
  Sun,
  Timer,
} from "lucide-react";
import type { QualityMetrics, ScannerStatus } from "../types";

interface QualityBannerProps {
  status: ScannerStatus;
  quality?: QualityMetrics;
  isStable: boolean;
  countdownSeconds?: number;
}

export const QualityBanner: React.FC<QualityBannerProps> = ({
  status,
  quality,
  isStable,
  countdownSeconds,
}) => {
  if (status === "auto_countdown") {
    return (
      <div className="scanner-pill is-countdown">
        <Timer size={15} className="animate-pulse" />
        <span>Hold steady... Capturing in {countdownSeconds ?? 1}s</span>
      </div>
    );
  }

  if (isStable) {
    return (
      <div className="scanner-pill is-ready">
        <CheckCircle2 size={15} />
        <span>Document ready for capture</span>
      </div>
    );
  }

  if (quality?.warningMessage) {
    const msg = quality.warningMessage;
    let Icon = AlertCircle;
    if (msg.includes("closer") || msg.includes("away")) {
      Icon = Maximize2;
    } else if (msg.includes("glare") || msg.includes("reflections")) {
      Icon = Sparkles;
    } else if (msg.includes("light") || msg.includes("dim")) {
      Icon = Sun;
    }

    return (
      <div className={`scanner-pill ${quality.overallQuality === "poor" ? "is-warning" : "is-info"}`}>
        <Icon size={15} />
        <span>{msg}</span>
      </div>
    );
  }

  return (
    <div className="scanner-pill is-searching">
      <ScanSearch size={15} />
      <span>Align document or card in camera frame</span>
    </div>
  );
};
