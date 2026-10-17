import { useParams } from "react-router";

import { Failed, PageHeader } from "@/components/layout/States";
import { MoreLoader } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { useShelfPages } from "@/lib/queries";

/** A shelf of the front page, or a genre: every video of it, as a grid that loads a page at a time. */
function ShelfScreen({ query, eyebrow }: { query: ReturnType<typeof useShelfPages>; eyebrow: string }) {
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
  return <ShelfScreen query={useShelfPages("category", id)} eyebrow={id === "hot" ? "Category" : "Genre"} />;
}
