import { type KeyboardEvent, type MouseEvent } from "react";
import { useNavigate } from "react-router";

import { Hint } from "@/components/ui/hint";
import { cn } from "@/lib/utils";

/** What each badge says, the same on a card and on a video's page (the server's tags.py). */
export const TAGS: Record<string, { label: string; short?: string; hint: string }> = {
  explicit: { label: "Explicit", short: "E", hint: "Its words are marked explicit by YouTube or Apple Music." },
  "fan-upload": { label: "Fan upload", hint: "Uploaded by someone other than the artist or their label." },
  apple: { label: "Apple Music", short: "APPLE", hint: "From Apple Music's catalog: it plays here as Apple's 30 s preview until YouTube has it." },
  soon: { label: "Coming soon", short: "SOON", hint: "Announced, not out yet." },
  "in-library": { label: "In your library", hint: "Its song or its video is in one of your libraries." },
};

export function tagInfo(tag: string, label?: string) {
  const fixed = TAGS[tag];
  if (fixed) return fixed;
  if (tag.startsWith("lang:")) return { label: label ?? tag.slice(5), hint: `Sung in ${label ?? tag.slice(5)}, going by its lyrics.` };
  if (tag.startsWith("genre:")) return { label: tag.slice(6), hint: `Apple Music files it under ${tag.slice(6)}.` };
  return { label: label ?? tag, hint: "" };
}

const LOOK = {
  // On a card's picture: small, dark, legible over any frame.
  overlay: "rounded-md bg-black/65 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white/90 backdrop-blur-md hover:bg-black/85",
  // Beside a video's title.
  pill: "rounded-full border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground hover:border-rose-400/40 hover:text-foreground",
} as const;

/**
 * A badge that says one thing about a video, with a line on what it means,
 * and leads to every video that carries it too (/tags). Inside a card, which
 * is a link already, it is a link by its role: an <a> can't hold another.
 */
export function TagBadge({ tag, label, hint, look = "pill", tone, compact = false, className }: {
  tag: string;
  label?: string;
  hint?: string;
  look?: keyof typeof LOOK;
  tone?: "accent" | "warning";
  compact?: boolean;
  className?: string;
}) {
  const navigate = useNavigate();
  const info = tagInfo(tag, label);
  const go = (event: MouseEvent | KeyboardEvent) => {
    event.preventDefault();
    event.stopPropagation();
    navigate(`/tags?tag=${encodeURIComponent(tag)}`);
  };
  return (
    <Hint text={<><span className="font-medium">{info.label}.</span> {hint ?? info.hint}</>}>
      <span
        role="link"
        tabIndex={0}
        aria-label={`${info.label}: every video tagged so`}
        onClick={go}
        onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && go(event)}
        className={cn("cursor-pointer outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring", LOOK[look],
                      tone === "accent" && (look === "overlay" ? "bg-rose-500/85 text-white hover:bg-rose-500" : "border-rose-400/30 text-rose-300"),
                      tone === "warning" && look === "pill" && "border-warning/30 text-warning",
                      className)}
      >
        {compact ? info.short ?? info.label : info.label}
      </span>
    </Hint>
  );
}
