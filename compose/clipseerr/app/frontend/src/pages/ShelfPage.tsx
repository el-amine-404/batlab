import { Link, useParams } from "react-router";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { MoreLoader, useReveal } from "@/components/video/MoreLoader";
import { shelfIcon } from "@/components/video/ShelfRow";
import { Updated } from "@/components/video/Updated";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import type { HotArtist } from "@/lib/api";
import { useHotArtists, useShelfPages } from "@/lib/queries";
import { ArtistBubble, ArtistRail } from "@/pages/SearchPage";

/** In the row: the first ones; every one is on /genres/hot/artists. */
const IN_ROW = 40;

const asSummary = (artist: HotArtist) => ({ id: artist.id!, name: artist.name, thumbnail: artist.thumbnail });
const inHot = (count: number) => `${count} ${count === 1 ? "video" : "videos"} in Hot`;

/** Hot's artists with a YouTube Music page, the most videos in Hot first. */
function useLinkedHotArtists() {
  const query = useHotArtists();
  return { ...query, artists: (query.data?.artists ?? []).filter((a) => a.id) };
}

/** Every artist Hot has shown, most videos first: kept even when their videos have left it. */
function HotArtists() {
  const { artists } = useLinkedHotArtists();
  if (!artists.length) return null;
  const videos = new Map(artists.map((a) => [a.id, a.videos]));
  return (
    <div className="mb-8">
      <ArtistRail
        title="Artists"
        count={artists.length}
        artists={artists.slice(0, IN_ROW).map(asSummary)}
        notes={(artist) => inHot(videos.get(artist.id) ?? 0)}
        action={artists.length > IN_ROW && (
          <Link to="/genres/hot/artists" className="mr-1 rounded-md px-2 py-1 text-sm font-medium text-rose-300 hover:bg-accent">
            See all {artists.length}
          </Link>
        )}
      />
    </div>
  );
}

/** /genres/hot/artists: every artist of Hot, as a grid drawn a screen at a time. */
export function HotArtistsPage() {
  const { artists, isPending, error, refetch } = useLinkedHotArtists();
  const reveal = useReveal(artists.length, 60);
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader eyebrow={<Link to="/genres/hot" className="hover:underline">Hot</Link>} title="Artists"
                  description={`${artists.length} artists Hot has shown, the most videos first.`} />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] gap-2 sm:grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))]">
        {artists.slice(0, reveal.count).map((artist) => (
          <ArtistBubble key={artist.id} artist={asSummary(artist)} note={inHot(artist.videos)} />
        ))}
      </div>
      <MoreLoader shown={reveal.count} total={artists.length} hasMore={reveal.hasMore} loading={false} failed={false} load={reveal.more} />
    </>
  );
}

function TitleWithIcon({ shelf }: { shelf: { id: string; title: string } }) {
  const Icon = shelfIcon(shelf);
  return (
    <span className="flex items-center gap-2.5">
      <Icon className="size-6 shrink-0 text-rose-300" aria-hidden /> {shelf.title}
    </span>
  );
}

/** A shelf of the front page, or a genre: every video of it, as a grid that loads a page at a time. */
function ShelfScreen({ query, eyebrow, extra }: { query: ReturnType<typeof useShelfPages>; eyebrow: string; extra?: React.ReactNode }) {
  const { data, error, refetch, isPending, fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } = query;
  if (error && !data) return <Failed error={error} retry={() => void refetch()} />;
  const first = data?.pages[0];
  const items = data?.pages.flatMap((page) => page.items) ?? [];
  const total = data?.pages[data.pages.length - 1]?.total ?? 0;
  return (
    <>
      <PageHeader
        eyebrow={eyebrow}
        title={first ? <TitleWithIcon shelf={first} /> : <span className="skeleton inline-block h-8 w-64 rounded" />}
        description={first ? (
          <>
            {first.subtitle && <span className="block">{first.subtitle}</span>}
            <span className="mt-0.5 block text-xs text-muted-foreground/70">
              {total} videos{first.updated_at ? " · " : ""}<Updated at={first.updated_at} />
            </span>
          </>
        ) : " "}
      />
      {extra}
      {isPending ? <GridSkeleton /> : (
        <>
          <VideoGrid videos={items} />
          <MoreLoader shown={items.length} total={total} hasMore={!!hasNextPage} loading={isFetchingNextPage}
                      failed={isFetchNextPageError} load={() => void fetchNextPage()} />
        </>
      )}
    </>
  );
}

export function ShelfPage() {
  const { id = "" } = useParams();
  return <ShelfScreen query={useShelfPages("shelf", id)} eyebrow="Discover" />;
}

export function GenrePage() {
  const { id = "" } = useParams();
  return <ShelfScreen query={useShelfPages("category", id)} eyebrow={id === "hot" ? "Category" : "Genre"}
                      extra={id === "hot" ? <HotArtists /> : undefined} />;
}
