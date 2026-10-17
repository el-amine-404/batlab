/**
 * The marks of the sites a video links to, drawn here: no favicon is fetched
 * from YouTube or Apple, so opening a page tells them nothing.
 */
export function BrandIcon({ name, className = "size-4" }: { name: string; className?: string }) {
  if (name === "youtube") {
    return (
      <svg viewBox="0 0 24 24" className={className} aria-hidden>
        <rect x="1.5" y="4.5" width="21" height="15" rx="4.5" fill="#FF0033" />
        <path d="M10 8.8v6.4l5.6-3.2z" fill="#fff" />
      </svg>
    );
  }
  if (name === "youtube_music") {
    return (
      <svg viewBox="0 0 24 24" className={className} aria-hidden>
        <circle cx="12" cy="12" r="10.5" fill="#FF0033" />
        <circle cx="12" cy="12" r="5.6" fill="none" stroke="#fff" strokeWidth="1.3" />
        <path d="M10.4 9.6v4.8l4.1-2.4z" fill="#fff" />
      </svg>
    );
  }
  if (name === "apple") {
    return (
      <svg viewBox="0 0 24 24" className={className} aria-hidden>
        <defs>
          <linearGradient id="apple-music" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#FA5C74" />
            <stop offset="1" stopColor="#FA233B" />
          </linearGradient>
        </defs>
        <rect x="1.5" y="1.5" width="21" height="21" rx="5.5" fill="url(#apple-music)" />
        <path d="M15.6 6.2v8.3a2.1 2.1 0 1 1-1.3-1.9V8.6l-4.6 1v6.2a2.1 2.1 0 1 1-1.3-1.9V8.1z" fill="#fff" />
      </svg>
    );
  }
  return null;
}

export const SITE_NAMES: Record<string, string> = { youtube: "YouTube", youtube_music: "YouTube Music", apple: "Apple Music" };
