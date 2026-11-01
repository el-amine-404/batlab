import type { Appearance, Profile, Stage } from "@/lib/api";

export const STAGE: Record<Stage, string> = {
  held: "Ready to scan", queued: "Waiting", scanning: "Checking the file", decoding: "Opening the photo", detecting: "Finding the edges",
  rendering: "Straightening", ready: "Done", blocked: "Refused", failed: "Failed",
};
export const STEPS: { stage: Stage; label: string }[] = [
  { stage: "queued", label: "Waiting" }, { stage: "scanning", label: "Security check" }, { stage: "decoding", label: "Opening" },
  { stage: "detecting", label: "Finding edges" }, { stage: "rendering", label: "Straightening" }, { stage: "ready", label: "Done" },
];
export const PROFILES: { id: Profile; label: string; hint: string; filter: string }[] = [
  { id: "faithful", label: "Color", hint: "As photographed. For cards, IDs, anything with photos.", filter: "none" },
  { id: "paper", label: "Clean paper", hint: "White background, shadows gone. For letters, forms, receipts.", filter: "grayscale(1) contrast(1.35) brightness(1.1)" },
  { id: "mono", label: "Black & white", hint: "Pure black on white. For text-only pages; smallest files.", filter: "grayscale(1) contrast(6) brightness(1.15)" },
];
export const APPEARANCES: { id: Appearance; label: string }[] = [
  { id: "auto", label: "Same as the scan" }, { id: "color", label: "Color" }, { id: "gray", label: "Grayscale" }, { id: "mono", label: "Black & white" },
];
export const TIMINGS: Record<string, string> = {
  scanning: "Security check", decoding: "Opening", detecting: "Finding edges", orienting: "Orientation", rendering: "Straightening", edit: "Edit", reused: "Reused",
};
