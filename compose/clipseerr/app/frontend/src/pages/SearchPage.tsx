import { LoaderCircle, Search } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Empty, Failed, PageHeader } from "@/components/layout/States";
import { RailArrows, useRail } from "@/components/ui/rail";
import { Switch } from "@/components/ui/switch";
import { MoreLoader } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { errorText, type ArtistSummary, type SearchResults } from "@/lib/api";
import { prefetchArtist, useQuickSearch, useSearch } from "@/lib/queries";
import { cn } from "@/lib/utils";

export function ArtistBubble({ artist, note }: { artist: ArtistSummary; note?: string }) {
  return (
    <Link to={`/artist/${artist.id}`} onPointerEnter={() => prefetchArtist(artist.id)} onFocus={() => prefetchArtist(artist.id)}
          className="group flex w-28 shrink-0 flex-col items-center gap-2 rounded-xl p-2 text-center outline-none focus-visible:ring-[3px] focus-visible:ring-ring sm:w-32">
      <span className="flex size-24 items-center justify-center overflow-hidden rounded-full bg-secondary ring-1 ring-white/10 transition group-hover:ring-rose-400/50 sm:size-28">
        {artist.thumbnail ? (
          <img src={artist.thumbnail} alt="" loading="lazy" decoding="async" className="size-full object-cover" />
        ) : (
          // No picture on YouTube Music: the name's first letter, not an empty circle.
          <span aria-hidden className="text-3xl font-semibold text-muted-foreground">{artist.name.trim().charAt(0).toUpperCase()}</span>
        )}
      </span>
      <span className="w-full truncate text-sm font-medium">{artist.name}</span>
      {(note || artist.subscribers) && (
        <span className="-mt-1.5 truncate text-xs text-muted-foreground">{note ?? `${artist.subscribers} subscribers`}</span>
      )}
    </Link>
  );
}

/** A row of artists that scrolls sideways, with arrows on wide screens. */
export function ArtistRail({ title, count, artists, notes, action }: {
  title: string;
  count?: number;
  artists: ArtistSummary[];
  notes?: (artist: ArtistSummary) => string | undefined;
  action?: React.ReactNode;
}) {
  const rail = useRail(artists.length);
  return (
    <section>
      <div className="mb-2 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">
          {title} {count !== undefined && <span className="text-sm font-normal text-muted-foreground">· {count}</span>}
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          {action}
          <RailArrows rail={rail} />
        </div>
      </div>
      <div ref={rail.ref} onScroll={rail.measure} className="rail -mx-4 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
        {artists.map((artist) => (
          <ArtistBubble key={artist.id} artist={artist} note={notes?.(artist)} />
        ))}
      </div>
    </section>
  );
}

/** YouTube Music's answer first, then what only clipseerr's own index found. */
function merged(full: SearchResults, quick?: SearchResults): SearchResults {
  if (!quick) return full;
  const ids = new Set(full.videos.flatMap((v) => [v.id, v.youtube_id && `yt:${v.youtube_id}`]));
  const artistIds = new Set(full.artists.map((a) => a.id));
  return {
    ...full,
    artists: [...full.artists, ...quick.artists.filter((a) => !artistIds.has(a.id))],
    videos: [...full.videos, ...quick.videos.filter((v) => !ids.has(v.id) && !(v.youtube_id && ids.has(`yt:${v.youtube_id}`)))],
  };
}

const any = (found?: SearchResults) => !!found && (found.videos.length > 0 || found.artists.length > 0);

export function SearchPage() {
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const full = useSearch(q);
  const quick = useQuickSearch(q);
  // Every page loaded so far, as one answer; clipseerr's own extras only after YouTube's last page.
  const pages = full.data?.pages ?? [];
  const joined: SearchResults | undefined = pages.length
    ? { ...pages[0], videos: pages.flatMap((page) => page.videos), total: pages[pages.length - 1].total }
    : undefined;
  const fullNow = full.isPlaceholderData ? undefined : joined;
  const quickNow = quick.isPlaceholderData ? undefined : quick.data;
  // This search's full answer (with the index's extras); else the index's, at once; else the last search's, dimmed.
  const data = fullNow ? (full.hasNextPage ? fullNow : merged(fullNow, quickNow)) : any(quickNow) ? quickNow : (joined ?? quick.data);
  const early = !fullNow && any(quickNow);
  const { error, refetch } = full;
  const isFetching = !early && (full.isFetching || quick.isFetching);
  // The index found nothing yet: wait for YouTube Music before saying "Nothing found".
  const isPending = !data || (!fullNow && !early && !full.isPlaceholderData && full.isFetching);
  const [officialOnly, setOfficialOnly] = useState(() => localStorage.getItem("official-only") === "1");
  const toggleOfficial = (on: boolean) => {
    setOfficialOnly(on);
    try {
      localStorage.setItem("official-only", on ? "1" : "0");
    } catch {
      // private window: the choice lasts this visit
    }
  };
  const videos = (data?.videos ?? []).filter((v) => !officialOnly || v.official);

  if (!q) {
    return (
      <>
        <PageHeader eyebrow="Search" title="Find a video" description="By the song's name, the artist, or both." />
        <Empty icon={<Search />} title="Type above to search">Press <kbd className="rounded border border-border px-1">/</kbd> anywhere to jump to the search.</Empty>
      </>
    );
  }
  return (
    <div className={cn("transition-opacity", isFetching && data && "opacity-70")}>
      <PageHeader eyebrow="Search" title={`“${q}”`} />
      {early && (full.isFetching || error) && (
        <p className="-mt-2 mb-5 flex items-center gap-2 text-xs text-muted-foreground">
          {error ? (
            <>
              Only what clipseerr has shown so far: {errorText(error)}
              <button type="button" onClick={() => void refetch()} className="font-medium text-rose-300 hover:underline">Try again</button>
            </>
          ) : (
            <><LoaderCircle className="size-3.5 animate-spin" /> Shown so far by clipseerr; looking on YouTube Music…</>
          )}
        </p>
      )}
      {error && !early ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : isPending ? (
        <GridSkeleton />
      ) : !data.videos.length && !data.artists.length ? (
        <Empty icon={<Search />} title="Nothing found">Try fewer words, or the artist's name alone.</Empty>
      ) : (
        <div className="flex flex-col gap-8">
          {data.artists.length > 0 && <ArtistRail title="Artists" artists={data.artists} />}
          {data.videos.length > 0 && (
            <section>
              <div className="mb-3 flex items-center justify-between gap-4">
                <h2 className="text-lg font-semibold tracking-tight">
                  Videos <span className="text-sm font-normal text-muted-foreground">· {Math.max(data.total ?? 0, data.videos.length)}</span>
                </h2>
                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                  Official only <Switch checked={officialOnly} onCheckedChange={toggleOfficial} />
                </label>
              </div>
              {videos.length ? <VideoGrid videos={videos} /> : (
                <p className="py-10 text-center text-sm text-muted-foreground">No official video among these. Turn “Official only” off to see the rest.</p>
              )}
              {fullNow && (
                <MoreLoader shown={data.videos.length} total={Math.max(data.total ?? 0, data.videos.length)} hasMore={!!full.hasNextPage}
                            loading={full.isFetchingNextPage} failed={full.isFetchNextPageError} load={() => void full.fetchNextPage()} />
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
