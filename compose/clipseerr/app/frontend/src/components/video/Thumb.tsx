import { useState } from "react";

import { cn, ytImage } from "@/lib/utils";

/** A video's picture, 16:9, loaded when it scrolls near; YouTube's bigger sizes fall back to smaller ones. */
export function Thumb({ src, youtubeId, alt = "", size = "mq", className, eager = false }: {
  src?: string | null;
  youtubeId?: string | null;
  alt?: string;
  size?: "mq" | "hq" | "sd" | "maxres";
  className?: string;
  eager?: boolean;
}) {
  const sizes = youtubeId ? (["maxres", "sd", "hq", "mq"] as const).slice(["maxres", "sd", "hq", "mq"].indexOf(size)) : [];
  const [step, setStep] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const url = youtubeId && step < sizes.length ? ytImage(youtubeId, sizes[step]) : src;

  return (
    <div className={cn("relative aspect-video overflow-hidden bg-secondary", !loaded && "skeleton", className)}>
      {url && (
        <img
          src={url}
          alt={alt}
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={eager ? "high" : "auto"}
          onLoad={(event) => {
            // YouTube answers a missing maxres with a 120×90 grey picture, not an error.
            if (youtubeId && event.currentTarget.naturalWidth <= 120 && step < sizes.length - 1) setStep(step + 1);
            else setLoaded(true);
          }}
          onError={() => (youtubeId && step < sizes.length ? setStep(step + 1) : setLoaded(true))}
          className={cn("size-full object-cover transition-opacity duration-300", loaded ? "opacity-100" : "opacity-0")}
        />
      )}
    </div>
  );
}
