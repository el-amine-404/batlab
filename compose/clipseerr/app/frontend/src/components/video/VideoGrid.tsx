import type { Video } from "@/lib/api";

import { useRequested } from "./useRequested";
import { CardSkeleton, VideoCard } from "./VideoCard";

const GRID = "grid grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))] gap-x-4 gap-y-7";

export function VideoGrid({ videos }: { videos: Video[] }) {
  const requested = useRequested();
  return (
    <div className={GRID}>
      {videos.map((video, index) => (
        <VideoCard key={video.id} video={video} requested={requested.get(video.id)} eager={index < 8} />
      ))}
    </div>
  );
}

export function GridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <div className={GRID} aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <CardSkeleton key={i} />
      ))}
    </div>
  );
}
