import React from "react";
import type { Quad, ScannerStatus } from "../types";

interface ScannerOverlayProps {
  quad: Quad | null;
  status: ScannerStatus;
  isStable: boolean;
  aspectRatio: string;
}

export const ScannerOverlay: React.FC<ScannerOverlayProps> = ({
  quad,
  status,
  isStable,
}) => {
  const pointsString = quad
    ? quad.map(([x, y]) => `${(x * 100).toFixed(2)},${(y * 100).toFixed(2)}`).join(" ")
    : "";

  const isReady = isStable || status === "auto_countdown" || status === "ready";

  return (
    <svg
      className="scanner-overlay-svg"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
        <linearGradient id="readyGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#10b981" />
          <stop offset="100%" stopColor="#059669" />
        </linearGradient>
      </defs>

      {quad ? (
        <>
          {/* Translucent fill and animated boundary */}
          <polygon
            points={pointsString}
            className={`scanner-poly ${isReady ? "poly-ready" : "poly-tracking"}`}
            filter={isReady ? "url(#glow)" : undefined}
          />

          {/* 4 Corner Pin Circles */}
          {quad.map(([x, y], idx) => (
            <g key={idx} transform={`translate(${(x * 100).toFixed(2)}, ${(y * 100).toFixed(2)})`}>
              <circle
                r="3.2"
                className={`scanner-corner-outer ${isReady ? "corner-ready" : "corner-tracking"}`}
              />
              <circle r="1.6" className="scanner-corner-inner" />
            </g>
          ))}
        </>
      ) : (
        /* Subtle viewfinder guide frame when searching */
        <g className="scanner-guide-idle">
          <rect
            x="12"
            y="12"
            width="76"
            height="76"
            rx="5"
            className="scanner-idle-rect"
          />
          {/* Corner brackets */}
          <path d="M 12 24 L 12 12 L 24 12" className="scanner-corner-bracket" />
          <path d="M 76 12 L 88 12 L 88 24" className="scanner-corner-bracket" />
          <path d="M 88 76 L 88 88 L 76 88" className="scanner-corner-bracket" />
          <path d="M 24 88 L 12 88 L 12 76" className="scanner-corner-bracket" />
        </g>
      )}
    </svg>
  );
};
