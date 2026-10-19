/** The words the page uses for the API's values. */
import type { Job, Status, VideoOptions } from "@/lib/api";
import { clock } from "@/lib/utils";

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
  processing: "Working on the file",
  placing: "Moving it into place",
};

/**
 * The parts of processing, by the name the download reports (yt-dlp's
 * postprocessors, named without "FFmpeg"; then videodrop's own): what is
 * being done to the file.
 */
const STEPS: Record<string, string> = {
  Merger: "Joining the picture and the sound",
  FixupM3u8: "Repairing the file's container",
  FixupStretched: "Repairing the picture's shape",
  FixupDuplicateMoov: "Repairing the file's container",
  FixupTimestamp: "Repairing the timing",
  FixupDuration: "Repairing the length",
  FFmpegMetadata: "Writing the title, artist and chapters",
  FFmpegThumbnailsConvertor: "Preparing the cover",
  EmbedThumbnail: "Putting the cover in",
  FFmpegEmbedSubtitle: "Putting the subtitles in",
  FFmpegSubtitlesConvertor: "Converting the subtitles",
  FFmpegVideoRemuxer: "Changing the file type",
  FFmpegVideoConvertor: "Converting the video",
  FFmpegSplitChapters: "Splitting the chapters",
  SponsorBlock: "Finding the segments to cut",
  ModifyChapters: "Cutting segments",
  MoveFiles: "Moving the file",
  Collect: "Checking the file (ffprobe)",
  lyrics: "Looking up the lyrics",
  check: "Checking the file (ffprobe)",
};
export const STEP: Record<string, string> = {
  ...STEPS,
  ...Object.fromEntries(Object.entries(STEPS).filter(([key]) => key.startsWith("FFmpeg")).map(([key, text]) => [key.slice(6), text])),
};

/** What a job is called before its title is known. */
export function jobTitle(job: Job): string {
  return job.title ?? job.url.replace(/^https?:\/\/(www\.)?/, "");
}

/** "1:30 → 2:45": the part of a video a job asked for. */
export function sectionText(section: { start: number; end: number }): string {
  return `${clock(section.start)} → ${clock(section.end)}`;
}

/** Who asked: the person or the app's key; the client an app named, for downloads from before accounts. */
export function askedBy(job: Job): string | null {
  return job.asked_by?.name ?? job.client ?? null;
}
