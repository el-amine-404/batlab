import { ArrowDownAZ, CalendarArrowDown, Flame } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router";

import { Failed } from "@/components/layout/States";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import { useArtist } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { ArtistBubble } from "./SearchPage";

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

  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <div className="relative -mx-4 -mt-6 mb-8 overflow-hidden px-4 pt-10 pb-6 sm:-mx-6 sm:px-6 lg:-mx-10 lg:-mt-8 lg:px-10">
        {data?.thumbnail && (
          <img src={data.thumbnail} alt="" aria-hidden className="absolute inset-0 -z-10 size-full scale-110 object-cover opacity-25 blur-3xl" />
        )}
        <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-end">
          <div className="size-32 shrink-0 overflow-hidden rounded-full bg-secondary shadow-2xl ring-1 ring-white/15 sm:size-40">
            {data?.thumbnail ? <img src={data.thumbnail} alt="" className="size-full object-cover" /> : <div className="skeleton size-full" />}
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
      {isPending ? <GridSkeleton /> : <VideoGrid videos={videos} />}

      {!!data?.related?.length && (
        <section className="mt-12">
          <h2 className="mb-2 text-lg font-semibold tracking-tight">Fans also like</h2>
          <div className="rail -mx-4 flex gap-2 overflow-x-auto px-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
            {data.related?.map((artist) => (
              <ArtistBubble key={artist.id} artist={artist} />
            ))}
          </div>
        </section>
      )}
    </>
  );
}
