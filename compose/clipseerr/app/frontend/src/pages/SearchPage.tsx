import { Search } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Empty, Failed, PageHeader } from "@/components/layout/States";
import { Switch } from "@/components/ui/switch";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import type { ArtistSummary } from "@/lib/api";
import { prefetchArtist, useSearch } from "@/lib/queries";
import { cn } from "@/lib/utils";

export function ArtistBubble({ artist }: { artist: ArtistSummary }) {
  return (
    <Link to={`/artist/${artist.id}`} onPointerEnter={() => prefetchArtist(artist.id)} onFocus={() => prefetchArtist(artist.id)}
          className="group flex w-28 shrink-0 flex-col items-center gap-2 rounded-xl p-2 text-center outline-none focus-visible:ring-[3px] focus-visible:ring-ring sm:w-32">
      <span className="size-24 overflow-hidden rounded-full bg-secondary ring-1 ring-white/10 transition group-hover:ring-rose-400/50 sm:size-28">
        {artist.thumbnail && <img src={artist.thumbnail} alt="" loading="lazy" decoding="async" className="size-full object-cover" />}
      </span>
      <span className="w-full truncate text-sm font-medium">{artist.name}</span>
      {artist.subscribers && <span className="-mt-1.5 truncate text-xs text-muted-foreground">{artist.subscribers} subscribers</span>}
    </Link>
  );
}

export function SearchPage() {
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const { data, error, refetch, isFetching, isPending } = useSearch(q);
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
      {error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : isPending ? (
        <GridSkeleton />
      ) : !data.videos.length && !data.artists.length ? (
        <Empty icon={<Search />} title="Nothing found">Try fewer words, or the artist's name alone.</Empty>
      ) : (
        <div className="flex flex-col gap-8">
          {data.artists.length > 0 && (
            <section>
              <h2 className="mb-2 text-lg font-semibold tracking-tight">Artists</h2>
              <div className="rail -mx-4 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
                {data.artists.map((artist) => (
                  <ArtistBubble key={artist.id} artist={artist} />
                ))}
              </div>
            </section>
          )}
          {data.videos.length > 0 && (
            <section>
              <div className="mb-3 flex items-center justify-between gap-4">
                <h2 className="text-lg font-semibold tracking-tight">Videos</h2>
                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                  Official only <Switch checked={officialOnly} onCheckedChange={toggleOfficial} />
                </label>
              </div>
              {videos.length ? <VideoGrid videos={videos} /> : (
                <p className="py-10 text-center text-sm text-muted-foreground">No official video among these. Turn “Official only” off to see the rest.</p>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
