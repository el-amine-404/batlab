import { ArrowDownAZ, CalendarArrowDown, Disc3, Flame } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router";

import { Empty, Failed } from "@/components/layout/States";
import { RailArrows, useRail } from "@/components/ui/rail";
import { AlbumCard } from "@/components/video/AlbumCard";
import { MoreLoader, useReveal } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import type { AlbumSummary } from "@/lib/api";
import { useArtist } from "@/lib/queries";
import { cn, realPicture } from "@/lib/utils";

import { ArtistRail } from "./SearchPage";

function Albums({ albums }: { albums: AlbumSummary[] }) {
  const rail = useRail(albums.length);
  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">
          Albums and singles <span className="text-sm font-normal text-muted-foreground">· {albums.length}</span>
        </h2>
        <div className="flex shrink-0 items-center gap-1"><RailArrows rail={rail} /></div>
      </div>
      <div ref={rail.ref} onScroll={rail.measure} className="rail -mx-4 flex gap-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
        {albums.map((album) => <AlbumCard key={album.id} album={album} className="w-36 shrink-0 sm:w-44" />)}
      </div>
    </section>
  );
}

export function ArtistPage() {
  const { id = "" } = useParams();
  const { data, error, refetch, isPending } = useArtist(id);
  const [order, setOrder] = useState<"newest" | "popular" | "az">("newest");
  const [filter, setFilter] = useState("");
  const [more, setMore] = useState(false);

  const videos = useMemo(() => {
    let list = data?.videos ?? [];
    const words = filter.trim().toLowerCase();
    if (words) list = list.filter((v) => `${v.title} ${v.artist}`.toLowerCase().includes(words));
    if (order === "az") return [...list].sort((a, b) => a.title.localeCompare(b.title));
    // Newest first; the few without a date (a making-of, a live clip) after, as YouTube lists them.
    if (order === "newest") return [...list].sort((a, b) => (b.released ?? "").localeCompare(a.released ?? ""));
    return list;
  }, [data, order, filter]);

  const reveal = useReveal(videos.length, 48, `${id}|${order}|${filter}`);

  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <div className="relative -mx-4 -mt-6 mb-8 overflow-hidden px-4 pt-10 pb-6 sm:-mx-6 sm:px-6 lg:-mx-10 lg:-mt-8 lg:px-10">
        {realPicture(data?.thumbnail) && (
          <img src={realPicture(data?.thumbnail)!} alt="" aria-hidden className="absolute inset-0 -z-10 size-full scale-110 object-cover opacity-25 blur-3xl" />
        )}
        <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-end">
          <div className="size-32 shrink-0 overflow-hidden rounded-full bg-secondary shadow-2xl ring-1 ring-white/15 sm:size-40">
            {realPicture(data?.thumbnail) ? <img src={realPicture(data?.thumbnail)!} alt="" className="size-full object-cover" />
              : data ? (
                // No picture on YouTube Music: the first letter, not a shimmer that looks like it is still loading.
                <span className="flex size-full items-center justify-center text-5xl font-semibold text-muted-foreground">
                  {data.name.trim().charAt(0).toUpperCase()}
                </span>
              ) : <div className="skeleton size-full" />}
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-xs font-medium tracking-wider text-rose-300/90 uppercase">Artist</p>
            <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">{data?.name ?? <span className="skeleton inline-block h-10 w-60 rounded" />}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {[data?.subscribers && `${data.subscribers} subscribers`, data && `${data.videos.length} videos`].filter(Boolean).join(" · ")}
            </p>
          </div>
        </div>
        {data?.description && (
          <p className={cn("mt-4 max-w-3xl text-sm whitespace-pre-line text-muted-foreground", !more && "line-clamp-2")}>
            {data.description}{" "}
          </p>
        )}
        {data?.description && data.description.length > 180 && (
          <button type="button" onClick={() => setMore(!more)} className="mt-1 text-xs font-medium text-rose-300">
            {more ? "Less" : "More"}
          </button>
        )}
      </div>

      {!!data?.albums?.length && <Albums albums={data.albums} />}

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight">Videos</h2>
        <div className="flex items-center gap-2">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter"
                 className="h-9 w-36 rounded-lg border border-input bg-black/20 px-3 text-sm outline-none focus-visible:border-ring sm:w-48" />
          <div className="inline-flex rounded-lg border border-border p-0.5">
            {([["newest", CalendarArrowDown, "Newest first"], ["popular", Flame, "Most popular, as YouTube lists them"], ["az", ArrowDownAZ, "A to Z"]] as const).map(([value, Icon, label]) => (
              <button key={value} type="button" title={label} aria-label={label} aria-pressed={order === value}
                      onClick={() => setOrder(value)}
                      className={cn("flex size-8 items-center justify-center rounded-md text-muted-foreground", order === value && "bg-accent text-foreground")}>
                <Icon className="size-4" />
              </button>
            ))}
          </div>
        </div>
      </div>
      {isPending ? <GridSkeleton /> : !data?.videos.length && !data?.albums?.length ? (
        <Empty icon={<Disc3 />} title="Nothing by them on YouTube Music">
          This channel has no videos or songs YouTube Music lists yet (an artist's auto-made "Topic" channel can be empty).{" "}
          <a href={`https://www.youtube.com/channel/${id}`} target="_blank" rel="noreferrer" className="text-rose-300 hover:underline">YouTube</a>
          {" · "}
          <a href={`https://music.youtube.com/channel/${id}`} target="_blank" rel="noreferrer" className="text-rose-300 hover:underline">YouTube Music</a>
        </Empty>
      ) : (
        <>
          <VideoGrid videos={videos.slice(0, reveal.count)} />
          <MoreLoader shown={reveal.count} total={videos.length} hasMore={reveal.hasMore} loading={false} load={reveal.more} />
        </>
      )}

      {!!data?.related?.length && (
        <div className="mt-12"><ArtistRail title="Fans also like" artists={data.related} /></div>
      )}
    </>
  );
}
