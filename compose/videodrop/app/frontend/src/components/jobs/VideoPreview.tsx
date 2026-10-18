import { AlertTriangle, ExternalLink, FileCheck2, Film, ListVideo, Loader2, Radio } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { Probe } from "@/lib/api";
import { clock, cn, formatBytes } from "@/lib/utils";

const CODECS: Record<string, string> = { avc1: "H.264", h264: "H.264", av01: "AV1", vp09: "VP9", vp9: "VP9", hev1: "H.265", hvc1: "H.265", mp4a: "AAC", opus: "Opus" };

function codec(name: string | null | undefined): string | null {
  if (!name || name === "none") return null;
  return CODECS[name.split(".")[0].toLowerCase()] ?? name.split(".")[0].toUpperCase();
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className="truncate text-sm tabular-nums">{children}</dd>
    </div>
  );
}

function date(yyyymmdd: string | null | undefined): string | null {
  if (!yyyymmdd || yyyymmdd.length !== 8) return null;
  const at = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6)}T12:00:00`);
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** What a link will download, read from the site before downloading: picture, title, source, format and size. */
export function VideoPreview({ probe, loading, error, holdHours }: { probe?: Probe; loading: boolean; error?: string; holdHours?: number }) {
  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div>
          <p className="font-medium">The site says: {error}</p>
          <p className="text-xs text-muted-foreground">It would most likely fail. Check the link, or try again later.</p>
        </div>
      </div>
    );
  }
  if (!probe) {
    return loading ? (
      <div className="flex items-center gap-3 rounded-xl border border-border bg-black/10 p-3">
        <div className="skeleton h-16 w-28 rounded-lg" />
        <div className="flex flex-1 flex-col gap-2">
          <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Reading the video from the site…</p>
          <div className="skeleton h-3 w-2/3 rounded" />
        </div>
      </div>
    ) : null;
  }
  const f = probe.format;
  const size = f?.size_bytes ? `${f.size_exact ? "" : "≈ "}${formatBytes(f.size_bytes)}` : "unknown";
  const video = f && [f.height ? `${f.width}×${f.height}` : null, f.fps ? `${Math.round(f.fps)} fps` : null, f.dynamic_range && f.dynamic_range !== "SDR" ? f.dynamic_range : null].filter(Boolean).join(" · ");
  return (
    <div className={cn("rounded-xl border border-border bg-black/10 p-3", loading && "opacity-70")}>
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative aspect-video w-full shrink-0 overflow-hidden rounded-lg bg-secondary sm:w-48">
          {probe.thumbnail ? <img src={probe.thumbnail} alt="" referrerPolicy="no-referrer" className="size-full object-cover" />
            : <Film className="absolute inset-0 m-auto size-6 text-muted-foreground" />}
          {probe.duration ? <span className="absolute right-1 bottom-1 rounded bg-black/75 px-1 text-[11px] font-medium">{clock(probe.duration)}</span> : null}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1 font-medium break-words">{probe.title}</p>
            {loading && <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />}
          </div>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {probe.uploader && (probe.channel_url
              ? <a href={probe.channel_url} target="_blank" rel="noreferrer" className="hover:text-foreground hover:underline">{probe.uploader}</a>
              : <span>{probe.uploader}</span>)}
            {date(probe.upload_date) && <span>· {date(probe.upload_date)}</span>}
            {probe.views != null && <span>· {probe.views.toLocaleString()} views</span>}
            {probe.is_live && <Badge variant="destructive"><Radio /> Live now</Badge>}
            {!!probe.age_limit && <Badge variant="warning">{probe.age_limit}+: needs cookies</Badge>}
          </p>
          {probe.kind === "playlist" ? (
            <div className="text-sm">
              <p className="flex items-center gap-1.5"><ListVideo className="size-4 text-muted-foreground" />
                Playlist: {probe.count} videos{probe.taken !== probe.count ? `, the first ${probe.taken} taken` : ""}</p>
              <ol className="mt-1 list-decimal pl-5 text-xs text-muted-foreground">
                {(probe.entries ?? []).slice(0, 5).map((entry, n) => <li key={n} className="truncate">{entry.title}</li>)}
              </ol>
            </div>
          ) : (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
              <Fact label="Picture">{video || "—"}</Fact>
              <Fact label="Video">{[codec(f?.vcodec), f?.video_kbps ? `${Math.round(f.video_kbps)} kb/s` : null].filter(Boolean).join(" · ") || "—"}</Fact>
              <Fact label="Sound">{[codec(f?.acodec), f?.audio_kbps ? `${Math.round(f.audio_kbps)} kb/s` : null].filter(Boolean).join(" · ") || "none"}</Fact>
              <Fact label="Size">{size} · {(f?.container ?? "").toUpperCase()}</Fact>
            </dl>
          )}
          <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
            Source: {probe.extractor}
            {probe.webpage_url && <a href={probe.webpage_url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 truncate hover:text-foreground hover:underline">
              {probe.webpage_url.replace(/^https?:\/\//, "")} <ExternalLink className="size-3 shrink-0" />
            </a>}
            {!!probe.heights?.length && <span>· the site has {probe.heights.slice(0, 6).map((h) => `${h}p`).join(", ")}</span>}
          </p>
        </div>
      </div>
      {probe.target && (
        <p className={cn("mt-3 flex items-start gap-1.5 border-t border-border pt-2 text-xs", probe.target.exists ? "text-warning" : "text-muted-foreground")}>
          <FileCheck2 className="mt-px size-3.5 shrink-0" />
          {probe.target.path
            ? <span className="min-w-0 break-all">{probe.target.exists ? "Already there, it would be kept as it is: " : "Saved as "}<code>media/{probe.target.path}</code></span>
            : <span>Kept here for {holdHours ?? 24} h as <code>{probe.filename?.split("/").pop()}</code>, to download from this page.</span>}
        </p>
      )}
    </div>
  );
}
