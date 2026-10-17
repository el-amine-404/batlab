import { CalendarClock } from "lucide-react";
import { useMemo, useState } from "react";

import { Empty, Failed, PageHeader } from "@/components/layout/States";
import { Updated } from "@/components/video/Updated";
import { MoreLoader } from "@/components/video/MoreLoader";
import { byDay } from "@/components/video/Releases";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { useShelfPages } from "@/lib/queries";
import { cn } from "@/lib/utils";

type Source = "all" | "youtube" | "apple";

/** Everything out in the last weeks, and what is about to come out: newest first, by day, a page at a time. */
export function ReleasesPage() {
  const [source, setSource] = useState<Source>("all");
  const { data, error, refetch, isPending, fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } =
    useShelfPages("shelf", "releases", source === "all" ? undefined : source);
  const items = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data]);
  const groups = useMemo(() => byDay(items), [items]);
  const first = data?.pages[0];
  const total = data?.pages[data.pages.length - 1]?.total ?? 0;

  if (error && !data) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader
        eyebrow="Discover"
        title="Latest releases"
        description={first ? (
          <>
            <span className="block">{first.subtitle}</span>
            <span className="mt-0.5 block text-xs text-muted-foreground/70">
              {total} videos{first.updated_at ? " · " : ""}<Updated at={first.updated_at} />
            </span>
          </>
        ) : " "}
        actions={
          <div className="inline-flex rounded-lg border border-border p-0.5" role="tablist" aria-label="Source">
            {(["all", "youtube", "apple"] as const).map((value) => (
              <button key={value} type="button" role="tab" aria-selected={source === value} onClick={() => setSource(value)}
                      className={cn("h-8 rounded-md px-3 text-sm text-muted-foreground", source === value && "bg-accent text-foreground")}>
                {value === "all" ? "All" : value === "youtube" ? "YouTube" : "Apple Music"}
              </button>
            ))}
          </div>
        }
      />
      {isPending ? (
        <GridSkeleton />
      ) : !groups.length ? (
        <Empty icon={<CalendarClock />} title="Nothing here">No release in the last 30 days from this source.</Empty>
      ) : (
        <>
          <div className="flex flex-col gap-10">
            {groups.map((group) => (
              <section key={group.key} aria-label={group.label}>
                <h2 className={cn("mb-3 text-lg font-semibold tracking-tight", group.key === "soon" && "text-rose-200")}>
                  {group.label} <span className="text-sm font-normal text-muted-foreground">· {group.items.length}</span>
                </h2>
                <VideoGrid videos={group.items} />
              </section>
            ))}
          </div>
          <MoreLoader shown={items.length} total={total} hasMore={!!hasNextPage} loading={isFetchingNextPage}
                      failed={isFetchNextPageError} load={() => void fetchNextPage()} />
        </>
      )}
    </>
  );
}
