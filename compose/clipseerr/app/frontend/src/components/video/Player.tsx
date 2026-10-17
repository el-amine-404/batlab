import { ExternalLink, Film, Music2, Play, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

import type { VideoDetail } from "@/lib/api";
import { listen, playback } from "@/lib/playback";
import { cn } from "@/lib/utils";

import { Thumb } from "./Thumb";

type Mode = "video" | "audio" | "preview";

function embed(id: string) {
  // enablejsapi: the player tells its time (lib/playback.ts), for the lyrics to follow.
  const params = new URLSearchParams({ autoplay: "1", rel: "0", playsinline: "1", modestbranding: "1", iv_load_policy: "3",
                                       enablejsapi: "1", origin: window.location.origin });
  return `https://www.youtube-nocookie.com/embed/${id}?${params}`;
}

/**
 * Watch the video, or listen to the song (its official audio on YouTube
 * Music), or Apple's 30 s preview. Nothing loads from YouTube until play is
 * pressed: the page stays light, and nothing plays by surprise.
 */
export function Player({ video }: { video: VideoDetail }) {
  const modes: Mode[] = [];
  if (video.youtube_id && video.embeddable) modes.push("video");
  if (video.audio) modes.push("audio");
  if (video.preview_url) modes.push("preview");
  const [mode, setMode] = useState<Mode | null>(modes[0] ?? null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setMode(modes[0] ?? null);
    setPlaying(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [video.id]);

  // The details came: the video may turn out not to play elsewhere, or a song to be found.
  useEffect(() => {
    if (mode && !modes.includes(mode)) setMode(modes[0] ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modes.join()]);

  const choose = (next: Mode) => {
    setMode(next);
    setPlaying(true);
  };

  const source = mode === "audio" ? video.audio?.youtube_id : video.youtube_id;

  // Nothing plays any more: whoever follows the player stops.
  useEffect(() => () => {
    playback.attach(null);
    playback.set({ mode: null, playing: false, time: 0, videoId: null });
  }, [video.id]);

  return (
    <div className="flex flex-col gap-3">
      <div className="relative overflow-hidden rounded-2xl bg-black shadow-2xl shadow-black/50 ring-1 ring-white/10">
        {playing && mode === "preview" && video.preview_url ? (
          <video src={video.preview_url} poster={video.thumbnail ?? undefined} controls autoPlay playsInline className="aspect-video w-full bg-black" />
        ) : playing && mode && source ? (
          <iframe
            onLoad={(event) => listen(event.currentTarget, mode, source)}
            key={`${mode}-${source}`}
            src={embed(source)}
            title={`${video.artist} – ${video.title}`}
            className="aspect-video w-full"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            referrerPolicy="strict-origin-when-cross-origin"
            allowFullScreen
          />
        ) : (
          <button
            type="button"
            disabled={!mode}
            onClick={() => mode && choose(mode)}
            className="group relative block w-full text-left disabled:cursor-default"
            aria-label={mode ? `Play ${video.title}` : undefined}
          >
            <Thumb src={video.thumbnail} youtubeId={video.youtube_id} size="maxres" eager />
            <span className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
            {mode ? (
              <span className="absolute top-1/2 left-1/2 flex size-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-black shadow-2xl transition-transform group-hover:scale-110 sm:size-20">
                <Play className="ml-1 size-7 fill-current sm:size-8" />
              </span>
            ) : (
              <span className="absolute inset-x-0 bottom-0 p-5 text-sm text-white/85">YouTube doesn't let other pages play this one.</span>
            )}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        {modes.length > 0 && (
          <div className="inline-flex rounded-xl border border-border bg-black/20 p-1" role="tablist" aria-label="What to play">
            {modes.map((item) => {
              const Icon = item === "video" ? Film : item === "audio" ? Music2 : Sparkles;
              const label = item === "video" ? "Video" : item === "audio" ? "Song" : "30 s preview";
              return (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={mode === item}
                  onClick={() => choose(item)}
                  className={cn(
                    "flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-muted-foreground transition",
                    mode === item ? "bg-accent text-foreground shadow-sm" : "hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" /> {label}
                </button>
              );
            })}
          </div>
        )}
        <div className="flex flex-wrap gap-1">
          {Object.entries(video.links ?? {}).map(([name, href]) => (
            <a key={name} href={href} target="_blank" rel="noreferrer"
               className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground">
              {name === "youtube" ? "YouTube" : name === "youtube_music" ? "YouTube Music" : "Apple Music"}
              <ExternalLink className="size-3" />
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
