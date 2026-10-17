import { useParams } from "react-router";

import { Failed, PageHeader } from "@/components/layout/States";
import { MoreLoader } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { useHotArtists, useShelfPages } from "@/lib/queries";
import { ArtistBubble } from "@/pages/SearchPage";

/** Every artist Hot has shown, most videos first: kept even when their videos have left it. */
function HotArtists() {
  const { data } = useHotArtists();
  const artists = data?.artists ?? [];
  if (!artists.length) return null;
  return (
    <section className="mb-8" aria-labelledby="hot-artists">
      <h2 id="hot-artists" className="mb-2 text-lg font-semibold tracking-tight">
        Artists <span className="text-sm font-normal text-muted-foreground">· {artists.length}</span>
      </h2>
      <div className="rail -mx-4 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
        {artists.slice(0, 80).map((artist) =>
          artist.id ? (
            <ArtistBubble key={artist.name} artist={{ id: artist.id, name: artist.name, thumbnail: artist.thumbnail }}
                          note={`${artist.videos} ${artist.videos === 1 ? "video" : "videos"} in Hot`} />
          ) : null,
        )}
      </div>
    </section>
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
        title={first?.title ?? <span className="skeleton inline-block h-8 w-64 rounded" />}
        description={first ? (
          <>
            {first.subtitle && <span className="block">{first.subtitle}</span>}
            <span className="mt-0.5 block text-xs text-muted-foreground/70">{total} videos</span>
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
