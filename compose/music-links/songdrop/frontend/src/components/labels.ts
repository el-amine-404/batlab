import type { Engine } from "@/lib/api";

const KINDS: Record<string, string> = {
  track: "song",
  album: "album",
  playlist: "playlist",
  artist: "artist",
  media: "audio",
};

export function kindLabel(kind: string): string {
  return KINDS[kind] ?? kind;
}

export function engineVariant(engine: Engine, site: string): "spotify" | "youtube" | "site" {
  if (engine === "spotdl") return "spotify";
  return site.startsWith("YouTube") ? "youtube" : "site";
}
