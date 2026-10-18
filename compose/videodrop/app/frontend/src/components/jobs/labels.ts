/** The words the page uses for the API's values. */
import type { Job, Status, VideoOptions } from "@/lib/api";

export const QUALITY: Record<VideoOptions["quality"], string> = {
  best: "Best",
  "2160": "2160p (4K)",
  "1440": "1440p",
  "1080": "1080p",
  "720": "720p",
  "480": "480p",
  "360": "360p",
};

export const CODEC: Record<VideoOptions["codec"], string> = {
  any: "Any (best quality)",
  h264: "H.264 (plays everywhere)",
  h265: "H.265 / HEVC",
  vp9: "VP9",
  av1: "AV1",
};

export const CONTAINER: Record<VideoOptions["container"], string> = {
  mp4: "MP4",
  mkv: "MKV",
  webm: "WebM",
  original: "As the site sends it",
};

export const STATUS: Record<Status, { label: string; variant: "accent" | "success" | "destructive" | "muted" | "default" }> = {
  queued: { label: "Queued", variant: "default" },
  running: { label: "Downloading", variant: "accent" },
  finished: { label: "Done", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "muted" },
};

export const STAGE: Record<NonNullable<Job["stage"]>, string> = {
  starting: "Starting",
  downloading: "Downloading",
  processing: "Finishing the file",
  placing: "Moving it into place",
};

/** What a job is called before its title is known. */
export function jobTitle(job: Job): string {
  return job.title ?? job.url.replace(/^https?:\/\/(www\.)?/, "");
}
