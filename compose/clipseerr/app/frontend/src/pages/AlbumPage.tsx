import { Disc3 } from "lucide-react";
import { Link, useParams } from "react-router";

import { Failed, Loading } from "@/components/layout/States";
import { TagBadge } from "@/components/video/TagBadge";
import type { ArtistRef } from "@/lib/api";
import { prefetchVideo, useAlbum } from "@/lib/queries";
import { clock } from "@/lib/utils";

export function Credited({ artists, className }: { artists: ArtistRef[]; className?: string }) {
  return (
    <span className={className}>
      {artists.map((artist, i) => (
        <span key={`${artist.name}-${i}`}>
          {i > 0 && ", "}
          {artist.id ? (
            <Link to={`/artist/${artist.id}`} className="underline-offset-4 hover:text-rose-200 hover:underline">{artist.name}</Link>
          ) : artist.name}
        </span>
      ))}
    </span>
  );
}

/** /album/:id — an album, single or EP on YouTube Music, and its tracks: each opens its page. */
export function AlbumPage() {
  const { id = "" } = useParams();
  const { data, error, refetch, isPending } = useAlbum(id);
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const minutes = data.duration ? Math.round(data.duration / 60) : null;
  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-end">
        <div className="aspect-square w-48 shrink-0 overflow-hidden rounded-xl bg-secondary shadow-2xl ring-1 ring-white/10 sm:w-56">
          {data.thumbnail ? <img src={data.thumbnail} alt="" className="size-full object-cover" /> : <Disc3 className="m-auto size-16 text-muted-foreground" />}
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-xs font-medium tracking-wider text-rose-300/90 uppercase">{data.kind}</p>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">{data.title}</h1>
          <p className="mt-2 text-[15px] font-medium text-foreground/90"><Credited artists={data.artists} /></p>
          <p className="mt-1 text-sm text-muted-foreground">
            {[data.year, `${data.tracks.length} ${data.tracks.length === 1 ? "track" : "tracks"}`, minutes && `${minutes} min`].filter(Boolean).join(" · ")}
          </p>
        </div>
      </div>
      {data.description && <p className="mt-6 max-w-3xl text-sm whitespace-pre-line text-muted-foreground">{data.description}</p>}

      <ol className="mt-8 flex flex-col">
        {data.tracks.map((track) => (
          <li key={`${track.number}-${track.title}`}>
            {track.youtube_id ? (
              <Link to={`/watch/yt:${track.youtube_id}`} onPointerEnter={() => prefetchVideo(`yt:${track.youtube_id}`)}
                    className="group grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-accent/60">
                <TrackRow track={track} />
              </Link>
            ) : (
              <div className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 px-2 py-2.5 opacity-60">
                <TrackRow track={track} />
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function TrackRow({ track }: { track: AlbumPageTrack }) {
  return (
    <>
      <span className="text-right text-sm tabular-nums text-muted-foreground">{track.number ?? ""}</span>
      <span className="min-w-0">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium group-hover:text-foreground">{track.title}</span>
          {track.explicit && <TagBadge tag="explicit" compact />}
        </span>
        <span className="block truncate text-xs text-muted-foreground">{track.artists.map((a) => a.name).join(", ")}</span>
      </span>
      <span className="text-xs tabular-nums text-muted-foreground">{clock(track.duration)}</span>
    </>
  );
}

type AlbumPageTrack = NonNullable<ReturnType<typeof useAlbum>["data"]>["tracks"][number];
