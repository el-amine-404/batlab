import { useParams } from "react-router";

import { Failed, PageHeader } from "@/components/layout/States";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { useCategory, useShelf } from "@/lib/queries";

/** A shelf of the front page, or a genre: every video of it, as a grid. */
function ShelfScreen({ query, eyebrow }: { query: ReturnType<typeof useShelf>; eyebrow: string }) {
  const { data, error, refetch, isPending } = query;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader eyebrow={eyebrow} title={data?.title ?? <span className="skeleton inline-block h-8 w-64 rounded" />}
                  description={data ? `${data.items.length} videos${data.subtitle ? ` · ${data.subtitle}` : ""}` : " "} />
      {isPending ? <GridSkeleton /> : <VideoGrid videos={data!.items} />}
    </>
  );
}

export function ShelfPage() {
  const { id = "" } = useParams();
  return <ShelfScreen query={useShelf(id)} eyebrow="Discover" />;
}

export function GenrePage() {
  const { id = "" } = useParams();
  return <ShelfScreen query={useCategory(id)} eyebrow="Genre" />;
}
