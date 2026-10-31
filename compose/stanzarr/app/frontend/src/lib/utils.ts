import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

export function timeAgo(seconds: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, Math.round(now - seconds));
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  if (delta < 86400 * 60) return `${Math.round(delta / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** "in 12 min", "in 3 h", "now" when it is due. */
export function timeLeft(seconds: number, now = Date.now() / 1000): string {
  const delta = seconds - now;
  if (delta < 60) return "now";
  if (delta < 3600) return `in ${Math.round(delta / 60)} min`;
  return `in ${+(delta / 3600).toFixed(1)} h`;
}

/** 90 → "1.5 h"; for delays in minutes. */
export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${+(minutes / 60).toFixed(1)} h`;
  return `${+(minutes / 1440).toFixed(1)} d`;
}

/** Format seconds elapsed: 45 -> "45s", 130 -> "2m 10s", 3720 -> "1h 2m" */
export function formatDuration(seconds: number): string {
  if (seconds <= 0 || isNaN(seconds)) return "0s";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m < 60) return remS > 0 ? `${m}m ${remS}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM > 0 ? `${h}h ${remM}m` : `${h}h`;
}

export type JobProgress = {
  percent: number;
  label: string;
  step: number;
  totalSteps: number;
  estimatedRemainingSecs?: number;
};

/** Calculate pipeline progress (percent and step label) from the current stage text. */
export function getJobStageProgress(stage: string, kind = "ai_generate", startedAt?: number | null): JobProgress {
  const isGenerate = kind === "ai_generate";
  const stages = isGenerate
    ? [
        { match: "separating", label: "Stem separation (Demucs)", pct: 15 },
        { match: "transcribing", label: "Audio transcription (Whisper large-v3)", pct: 45 },
        { match: "whisper)", label: "Verification: Whisper Medium", pct: 65 },
        { match: "whisperL)", label: "Verification: Whisper Large-v3", pct: 80 },
        { match: "mms)", label: "Verification: Meta MMS CTC", pct: 90 },
        { match: "voting", label: "Timing consensus & voting", pct: 94 },
        { match: "encoding", label: "Stem encoding (Opus)", pct: 96 },
        { match: "uploading", label: "Uploading vocals", pct: 98 },
      ]
    : [
        { match: "separating", label: "Stem separation (Demucs)", pct: 20 },
        { match: "whisper)", label: "Verification: Whisper Medium", pct: 55 },
        { match: "whisperL)", label: "Verification: Whisper Large-v3", pct: 75 },
        { match: "mms)", label: "Verification: Meta MMS CTC", pct: 88 },
        { match: "voting", label: "Timing consensus & voting", pct: 93 },
        { match: "encoding", label: "Stem encoding (Opus)", pct: 96 },
        { match: "uploading", label: "Uploading vocals", pct: 98 },
      ];

  const totalSteps = stages.length;
  const sLower = (stage || "").toLowerCase();
  let found = { pct: 8, label: stage || "Starting", step: 1 };
  for (let i = stages.length - 1; i >= 0; i--) {
    if (sLower.includes(stages[i].match)) {
      found = { pct: stages[i].pct, label: stages[i].label, step: i + 1 };
      break;
    }
  }

  let estimatedRemainingSecs: number | undefined;
  if (startedAt && found.pct > 0) {
    const elapsed = Math.max(1, Date.now() / 1000 - startedAt);
    const estTotal = elapsed / (found.pct / 100);
    estimatedRemainingSecs = Math.max(5, Math.round(estTotal - elapsed));
  }

  return {
    percent: found.pct,
    label: found.label,
    step: found.step,
    totalSteps,
    estimatedRemainingSecs,
  };
}

/** The YouTube picture of a video, 320×180. */
export function ytImage(id: string): string {
  return `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
}
