/** Codecs as people know them (ffprobe's names in, a label out). */
const VIDEO: Record<string, string> = { h264: "H.264", hevc: "H.265", av1: "AV1", vp9: "VP9", vp8: "VP8", mpeg4: "MPEG-4" };
const AUDIO: Record<string, string> = { aac: "AAC", opus: "Opus", mp3: "MP3", vorbis: "Vorbis", flac: "FLAC", ac3: "AC-3", eac3: "E-AC-3", alac: "ALAC" };

export const videoName = (codec: string | null | undefined) => (codec ? VIDEO[codec] ?? codec.toUpperCase() : "–");
export const audioName = (codec: string | null | undefined) => (codec ? AUDIO[codec] ?? codec.toUpperCase() : "–");

/** 2160 → "4K", 1080 → "1080p": the name of a picture's height. */
export function definition(width: number | null | undefined, height: number | null | undefined): string | null {
  if (!width || !height) return null;
  const short = Math.min(width, height);
  return short >= 2160 ? "4K" : short >= 1440 ? "1440p" : short >= 1080 ? "1080p" : short >= 720 ? "720p" : `${short}p`;
}
