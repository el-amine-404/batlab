import { ArrowRight, Download, SearchX } from "lucide-react";
import { Link } from "react-router";

import { LinkForm } from "@/components/jobs/LinkForm";
import { useJobs } from "@/lib/jobs";

/** The first screen: a link in, nothing else in the way. */
export function AddPage() {
  const { refresh, queue, missing } = useJobs();

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 pt-[8vh]">
      <div className="text-center">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Paste a link, get the songs</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          A song, album or playlist from Spotify, YouTube, SoundCloud, Bandcamp and the other sites yt-dlp knows.
        </p>
      </div>
      <LinkForm onCreated={refresh} />
      <div className="flex flex-wrap justify-center gap-2 text-xs">
        {queue.length > 0 && (
          <Link to="/activity/queue" className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-muted-foreground hover:text-foreground">
            <Download className="size-3.5" /> {queue.length} in the queue <ArrowRight className="size-3" />
          </Link>
        )}
        {missing.length > 0 && (
          <Link to="/activity/missing" className="flex items-center gap-1.5 rounded-full border border-warning/30 px-3 py-1.5 text-warning hover:bg-warning/5">
            <SearchX className="size-3.5" /> {missing.length} missing song{missing.length === 1 ? "" : "s"} <ArrowRight className="size-3" />
          </Link>
        )}
      </div>
    </div>
  );
}
