import type { Appearance, Profile, Stage } from "@/lib/api";

export const STAGE: Record<Stage, string> = {
  queued: "Waiting", scanning: "Checking the file", decoding: "Opening the photo", detecting: "Finding the edges",
  rendering: "Straightening", ready: "Done", blocked: "Refused", failed: "Failed",
};
export const STEPS: { stage: Stage; label: string }[] = [
  { stage: "queued", label: "Waiting" }, { stage: "scanning", label: "Security check" }, { stage: "decoding", label: "Opening" },
  { stage: "detecting", label: "Finding edges" }, { stage: "rendering", label: "Straightening" }, { stage: "ready", label: "Done" },
];
export const PROFILES: { id: Profile; label: string; hint: string }[] = [
  { id: "faithful", label: "Color", hint: "Cards, IDs, anything with photos" },
  { id: "paper", label: "Clean paper", hint: "Letters, forms, receipts" },
  { id: "mono", label: "Black & white", hint: "Text-only pages, smallest" },
];
export const APPEARANCES: { id: Appearance; label: string }[] = [
  { id: "auto", label: "As the scan's finish" }, { id: "color", label: "Color" }, { id: "gray", label: "Grayscale" }, { id: "mono", label: "Black & white" },
];
export const TIMINGS: Record<string, string> = {
  scanning: "Security check", decoding: "Opening", detecting: "Finding edges", orienting: "Orientation", rendering: "Straightening", edit: "Edit", reused: "Reused",
};
