import type { Profile } from "@/lib/api";

/**
 * The same little letter as each look would make it: a photo of a page as it
 * is (cream, shadowed, coloured stamp and picture), cleaned (white, greys), or stark.
 */
export function LookSample({ look, className }: { look: Profile; className?: string }) {
  const color = look === "faithful", mono = look === "mono";
  const paper = color ? "#efe6d2" : "#ffffff";
  const ink = mono ? "#000000" : color ? "#2c3440" : "#3a3a3a";
  const line = mono ? "#000000" : color ? "#6b7280" : "#8f8f8f";
  const picture = color ? "#3f8f88" : mono ? "#000000" : "#9a9a9a";
  const sky = color ? "#a6d5e3" : mono ? "#ffffff" : "#d4d4d4";
  const stamp = color ? "#c2410c" : mono ? "#000000" : "#6b6b6b";
  const id = `shade-${look}`;
  return (
    <svg viewBox="0 0 120 150" className={className} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.45" stopColor="#000" stopOpacity="0" />
          <stop offset="1" stopColor="#3b2a12" stopOpacity="0.34" />
        </linearGradient>
      </defs>
      <rect x="6" y="4" width="108" height="142" rx="3" fill={paper} />
      <rect x="16" y="15" width="52" height="7" rx="1.5" fill={ink} />
      <rect x="16" y="27" width="34" height="3.5" rx="1" fill={line} />
      <g>
        <rect x="16" y="40" width="88" height="40" rx="2" fill={sky} stroke={mono ? "#000" : "none"} strokeWidth="1.4" />
        <path d="M16 80l24-22 16 13 14-10 34 19z" fill={picture} />
        <circle cx="88" cy="51" r="5" fill={color ? "#f5b942" : mono ? "#000" : "#bdbdbd"} />
      </g>
      {[90, 98, 106, 114].map((y, index) => <rect key={y} x="16" y={y} width={[88, 80, 86, 60][index]} height="3.5" rx="1" fill={line} />)}
      <circle cx="90" cy="130" r="10" fill="none" stroke={stamp} strokeWidth="2.2" />
      <path d="M85 130l4 4 7-8" fill="none" stroke={stamp} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="16" y="127" width="36" height="3.5" rx="1" fill={line} />
      {color && <rect x="6" y="4" width="108" height="142" rx="3" fill={`url(#${id})`} />}
    </svg>
  );
}
