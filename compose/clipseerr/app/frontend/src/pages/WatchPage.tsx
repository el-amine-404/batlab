import { CalendarClock, Check, ChevronDown, ExternalLink, Film, Flame, Music2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { Failed } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Player } from "@/components/video/Player";
import { RequestPanel } from "@/components/video/RequestPanel";
import { ShelfRow } from "@/components/video/ShelfRow";
import { Thumb } from "@/components/video/Thumb";
import type { Shelf, Version, Video, VideoDetail } from "@/lib/api";
import { CreditsPanel, LyricsPanel } from "@/components/video/SongInfo";
import { prefetchArtist, prefetchVideo, queryClient, useArtist, useHotPick, useHotPicks, useVersions, useVideo } from "@/lib/queries";
import { ago, clock, cn, day, upcoming } from "@/lib/utils";

/** The video as a list already showed it: the page shows that at once, while its details load. */
function listed(id: string): Video | undefined {
  for (const query of queryClient.getQueryCache().getAll()) {
    const data = query.state.data as { shelves?: Shelf[]; items?: Video[]; videos?: Video[]; pages?: Shelf[] } | undefined;
    if (!data || typeof data !== "object") continue;
    const lists = [...(data.shelves?.map((s) => s.items) ?? []), ...(data.pages?.map((p) => p.items) ?? []), data.items, data.videos];
    for (const list of lists) {
      const found = Array.isArray(list) ? list.find((v) => v?.id === id) : undefined;
      if (found) return found;
    }
  }
  return undefined;
}

function Meta({ video }: { video: Video }) {
  const soon = upcoming(video.released);
  const parts = [
    video.views && `${video.views} views`,
    video.released && (
      <span title={video.released}>
        {soon ? "Out " : ""}{day(video.released)} <span className={soon ? "text-rose-300" : "text-muted-foreground/70"}>({ago(video.released)})</span>
      </span>
    ),
    // Before it is out, Apple's length is its preview's.
    !soon && video.duration && clock(video.duration),
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm text-muted-foreground">
      {parts.map((part, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden className="text-muted-foreground/40">·</span>}
          {part}
        </span>
      ))}
      {soon && <Badge variant="accent">Coming soon</Badge>}
      {video.genre && <Badge variant="muted">{video.genre}</Badge>}
      {video.explicit && <Badge variant="muted">Explicit</Badge>}
      {!video.official && <Badge variant="warning">Fan upload</Badge>}
      {video.source === "apple" && <Badge variant="apple">Apple Music</Badge>}
    </div>
  );
}

function Artists({ video }: { video: Video }) {
  const linked = video.artists.filter((a) => a.id);
  if (!linked.length) return <span>{video.artist}</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-1">
      {linked.map((artist, i) => (
        <span key={artist.id} className="flex items-center gap-1">
          {i > 0 && <span className="text-muted-foreground">&</span>}
          <Link to={`/artist/${artist.id}`} onPointerEnter={() => prefetchArtist(artist.id!)}
                className="font-medium text-foreground/90 underline-offset-4 hover:text-rose-200 hover:underline">
            {artist.name}
          </Link>
        </span>
      ))}
    </span>
  );
}

/** Adds the video to the Hot category, or takes it out. */
function HotButton({ video }: { video: VideoDetail }) {
  const { data } = useHotPicks();
  const pick = useHotPick();
  if (!video.youtube_id) return null;
  const id = `yt:${video.youtube_id}`;
  const picked = data?.ids.includes(id) ?? false;
  return (
    <button type="button" disabled={!data || pick.isPending} onClick={() => pick.mutate({ id: picked ? id : video.id, add: !picked })}
            title={picked ? "Take it out of Hot" : "Add it to the Hot category"}
            className={cn("flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition",
                          picked ? "border-orange-400/40 bg-orange-400/15 text-orange-200" : "border-border text-muted-foreground hover:border-orange-400/40 hover:text-orange-200")}>
      {picked ? <Check className="size-3.5" /> : <Flame className="size-3.5" />} {picked ? "In Hot" : "Add to Hot"}
    </button>
  );
}

/** An Apple Music video YouTube doesn't have (yet): where the full one is. */
function NotOnYouTube({ video }: { video: VideoDetail }) {
  if (video.youtube_id) return null;
  const soon = upcoming(video.released);
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-rose-400/20 bg-rose-400/5 p-4 sm:flex-row sm:items-center">
      <CalendarClock className="size-5 shrink-0 text-rose-300" />
      <p className="flex-1 text-sm text-muted-foreground">
        {soon ? (
          <>
            <span className="font-medium text-foreground">Not out yet: it comes out {day(video.released)}, {ago(video.released)}.</span>{" "}
            Until then there is only Apple's preview. clipseerr looks for the full video on YouTube again every few hours.
          </>
        ) : (
          <>
            <span className="font-medium text-foreground">YouTube doesn't have this video.</span>{" "}
            The full one plays on Apple Music (with a subscription); here there is Apple's preview.
          </>
        )}
      </p>
      {video.links?.apple && (
        <a href={video.links.apple} target="_blank" rel="noreferrer"
           className="flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border px-3 text-sm font-medium hover:bg-accent">
          Apple Music <ExternalLink className="size-3.5" />
        </a>
      )}
    </div>
  );
}

function VersionRow({ version, current }: { version: Version; current?: boolean }) {
  const Icon = version.kind === "video" ? Film : Music2;
  const timer = useRef<number | undefined>(undefined);
  return (
    <Link
      to={`/watch/${version.id}`}
      onPointerEnter={() => {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => prefetchVideo(version.id), 120);
      }}
      onPointerLeave={() => window.clearTimeout(timer.current)}
      className="group flex items-center gap-3 rounded-lg border border-border bg-card/60 p-2 pr-3 transition hover:border-rose-400/30 hover:bg-accent/60"
    >
      <div className="relative w-20 shrink-0 overflow-hidden rounded-md">
        <Thumb src={version.thumbnail} youtubeId={version.kind === "video" ? version.youtube_id : undefined} />
        <span className="absolute right-1 bottom-1 flex size-5 items-center justify-center rounded bg-black/70 text-white"><Icon className="size-3" /></span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{version.original_title ?? version.title}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge variant={version.label === "Clean" ? "success" : version.label === "Explicit" ? "warning" : "muted"}>{version.label}</Badge>
          {version.explicit && version.label !== "Explicit" && <Badge variant="muted">Explicit</Badge>}
          {current && <Badge variant="accent">The song here</Badge>}
          {!!version.duration && <span className="text-xs text-muted-foreground">{clock(version.duration)}</span>}
        </div>
      </div>
    </Link>
  );
}

/** The song's other takes: clean or explicit, the official video, remixes, live. */
function OtherVersions({ video }: { video: VideoDetail }) {
  const { data } = useVersions(video.id, !!video.youtube_id);
  if (!data || (!data.videos.length && !data.songs.length)) return null;
  return (
    <section aria-labelledby="versions">
      <h2 id="versions" className="mb-1 text-lg font-semibold tracking-tight">Other versions</h2>
      <p className="mb-3 text-xs text-muted-foreground">The same song, another cut. Open one to get that one.</p>
      <div className="grid gap-6 md:grid-cols-2">
        {([["Videos", data.videos], ["Songs", data.songs]] as const).map(([title, list]) =>
          list.length ? (
            <div key={title}>
              <p className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">{title}</p>
              <div className="flex flex-col gap-2">
                {list.map((version) => (
                  <VersionRow key={version.id} version={version} current={version.youtube_id === video.audio?.youtube_id} />
                ))}
              </div>
            </div>
          ) : null,
        )}
      </div>
    </section>
  );
}

function MoreFrom({ video }: { video: VideoDetail }) {
  const artistId = video.artists.find((a) => a.id)?.id ?? "";
  const { data } = useArtist(artistId, !!artistId);
  const shelf = useMemo<Shelf | null>(() => {
    if (!data) return null;
    const items = data.videos.filter((v) => v.youtube_id !== video.youtube_id).slice(0, 20);
    return items.length ? { id: `artist-${data.id}`, title: `More from ${data.name}`, source: "youtube", items, total: items.length } : null;
  }, [data, video.youtube_id]);
  return shelf ? <ShelfRow shelf={shelf} /> : null;
}

export function WatchPage() {
  const { id = "" } = useParams();
  const { data, error, refetch } = useVideo(id);
  const preview = useMemo(() => (data ? undefined : listed(id)), [data, id]);
  const video = data ?? preview;
  const [open, setOpen] = useState(false);

  if (error && !video) return <Failed error={error} retry={() => void refetch()} />;

  return (
    <div className="flex flex-col gap-10">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] xl:gap-8">
        <div className="flex min-w-0 flex-col gap-5">
          {data ? (
            <Player video={data} />
          ) : (
            <div className="overflow-hidden rounded-2xl ring-1 ring-white/10">
              <Thumb src={video?.thumbnail} youtubeId={video?.youtube_id} size="maxres" eager />
            </div>
          )}
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl leading-tight font-semibold tracking-tight sm:text-3xl">
              {video?.title ?? <span className="skeleton inline-block h-8 w-72 rounded" />}
            </h1>
            <div className="text-[15px]">{video ? <Artists video={data ?? video} /> : <span className="skeleton inline-block h-4 w-40 rounded" />}</div>
            {video && <Meta video={data ?? video} />}
            {data && <div className="mt-1 flex flex-wrap gap-2"><HotButton video={data} /></div>}
            {data?.audio && (
              <p className="text-xs text-muted-foreground">
                The song: <span className="text-foreground/80">{data.audio.title}</span>
                {data.audio.album && <> · from <span className="text-foreground/80">{data.audio.album}</span></>}
                {data.audio.duration && <> · {clock(data.audio.duration)}</>}
              </p>
            )}
            {video?.original_title && <p className="text-xs text-muted-foreground/70">Uploaded as “{video.original_title}”</p>}
          </div>
          {data && <NotOnYouTube video={data} />}
          {data?.description && data.description.trim().length > 60 && (
            <div className="rounded-xl border border-border bg-card/60 p-4">
              <p className={cn("text-sm whitespace-pre-line text-muted-foreground", !open && "line-clamp-3")}>{data.description}</p>
              <button type="button" onClick={() => setOpen(!open)} className="mt-2 flex items-center gap-1 text-xs font-medium text-rose-300">
                {open ? "Less" : "More"} <ChevronDown className={cn("size-3.5 transition", open && "rotate-180")} />
              </button>
            </div>
          )}
        </div>

        <aside className="lg:sticky lg:top-24 lg:self-start">
          {data ? <RequestPanel video={data} /> : error ? <Failed error={error} retry={() => void refetch()} /> : (
            <div className="flex flex-col gap-3">
              <div className="skeleton h-40 rounded-xl" />
              <div className="skeleton h-40 rounded-xl" />
            </div>
          )}
        </aside>
      </div>
      {data && (data.youtube_id || data.audio) && (
        <div className="grid gap-4 md:grid-cols-2">
          <LyricsPanel video={data} />
          <CreditsPanel video={data} />
        </div>
      )}
      {data && <OtherVersions video={data} />}
      {data && <MoreFrom video={data} />}
    </div>
  );
}
