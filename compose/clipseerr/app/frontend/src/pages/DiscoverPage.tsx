import { Disc3 } from "lucide-react";
import { Link } from "react-router";

import { Empty, Failed, PageHeader } from "@/components/layout/States";
import { ReleaseRail } from "@/components/video/Releases";
import { ShelfRow, ShelfSkeleton } from "@/components/video/ShelfRow";
import { useCategories, useHome, useReleases } from "@/lib/queries";

function GenreChips() {
  const { data } = useCategories();
  // One chip per name: YouTube's and Apple's genres overlap (Pop, Rock…); YouTube's come first.
  const seen = new Set<string>();
  const chips = (data?.flatMap((group) => group.categories) ?? [])
    .filter((category) => {
      const name = category.title.toLowerCase().replace(/[^a-z]/g, "");
      if (seen.has(name)) return false;
      seen.add(name);
      return true;
    })
    .slice(0, 16);
  if (!chips.length) return null;
  return (
    <div className="rail -mx-4 mb-8 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:flex-wrap lg:px-0">
      {chips.map((category) => (
        <Link key={category.id} to={`/genres/${category.id}`}
              className="shrink-0 rounded-full border border-border bg-secondary/60 px-3.5 py-1.5 text-sm text-foreground/90 transition hover:border-rose-400/40 hover:bg-accent">
          {category.title}
        </Link>
      ))}
      <Link to="/genres" className="shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium text-rose-300 hover:bg-accent">
        All genres
      </Link>
    </div>
  );
}

export function DiscoverPage() {
  const { data, error, refetch, isPending } = useHome();
  const releases = useReleases();
  return (
    <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0">
        <PageHeader eyebrow="Discover" title="The newest music videos" description="What came out, and what everyone watches. Open one to watch it, then get the song or the video." />
        <GenreChips />
        {/* Below xl the releases are a list of their own, before the shelves. */}
        <ReleaseRail items={releases.data?.items} isPending={releases.isPending} limit={12} className="mb-10 max-h-[26rem] xl:hidden" />
        {isPending ? (
          <div className="flex flex-col gap-10">
            <ShelfSkeleton />
            <ShelfSkeleton />
            <ShelfSkeleton />
          </div>
        ) : error ? (
          <Failed error={error} retry={() => void refetch()} />
        ) : !data.shelves.length ? (
          <Empty icon={<Disc3 />} title="Nothing to show yet">YouTube Music and Apple Music didn't answer. Try again in a minute.</Empty>
        ) : (
          <div className="flex flex-col gap-10">
            {data.shelves.map((shelf, index) => (
              <ShelfRow key={shelf.id} shelf={shelf} eager={index === 0} />
            ))}
          </div>
        )}
      </div>
      <aside className="hidden xl:block">
        <ReleaseRail items={releases.data?.items} isPending={releases.isPending} className="sticky top-24 max-h-[calc(100dvh-7rem)]" />
      </aside>
    </div>
  );
}
