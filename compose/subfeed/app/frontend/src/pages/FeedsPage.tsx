import { Pencil, Plus, RotateCw, Rss } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { FeedDialog } from "@/components/feeds/FeedDialog";
import { FeedBadge, LastRead } from "@/components/feeds/FeedStatus";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Feed } from "@/lib/api";
import { useFeeds, useFeedsChanged } from "@/lib/queries";
import { cn } from "@/lib/utils";

function FeedCard({ feed, onEdit }: { feed: Feed; onEdit: () => void }) {
  const changed = useFeedsChanged();
  const run = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(errorText(error));
    }
    changed();
  };

  return (
    <li className={cn("flex flex-col gap-3 rounded-xl border border-border bg-card p-4", !feed.enabled && "opacity-60")}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/feeds/${feed.slug}`} className="truncate text-lg font-semibold hover:underline">{feed.title}</Link>
            <FeedBadge feed={feed} />
          </div>
          {feed.home_url && (
            <a href={feed.home_url} target="_blank" rel="noreferrer" className="text-xs text-orange-300 hover:underline">
              r/{feed.target}
            </a>
          )}
        </div>
        <Switch checked={feed.enabled} aria-label={feed.enabled ? "Stop reading it" : "Read it"}
                onCheckedChange={(enabled) => void run(() => api.updateFeed(feed.slug, { enabled }))} />
      </div>
      <dl className="flex gap-6 text-sm">
        <div><dt className="text-xs text-muted-foreground">Posts</dt><dd className="font-semibold tabular-nums">{feed.posts.toLocaleString()}</dd></div>
        <div><dt className="text-xs text-muted-foreground">With a video</dt><dd className="font-semibold tabular-nums">{feed.videos.toLocaleString()}</dd></div>
      </dl>
      <LastRead feed={feed} />
      <div className="mt-auto flex gap-2">
        <Button size="sm" variant="secondary" asChild><Link to={`/feeds/${feed.slug}`}>Posts</Link></Button>
        <Button size="sm" variant="ghost" disabled={!feed.enabled} onClick={() => void run(() => api.refreshFeed(feed.slug), "Reading it now")}>
          <RotateCw /> Read now
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}><Pencil /> Change</Button>
      </div>
    </li>
  );
}

export function FeedsPage() {
  const { data, error, isPending, refetch } = useFeeds();
  const [editing, setEditing] = useState<Feed | undefined>();
  const [open, setOpen] = useState(false);

  const add = (
    <Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus /> New feed</Button>
  );
  return (
    <>
      <PageHeader title="Feeds" actions={add}
                  description="The communities subfeed reads. Each one is a feed any program can read: JSON Feed, Atom or the API." />
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.feeds.length ? (
        <Empty icon={<Rss />} title="No feed yet">
          Give subfeed a subreddit: it takes its history, then reads what is new every half hour.
        </Empty>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {data.feeds.map((feed) => <FeedCard key={feed.slug} feed={feed} onEdit={() => { setEditing(feed); setOpen(true); }} />)}
        </ul>
      )}
      <FeedDialog saved={editing} open={open} onOpenChange={setOpen} />
    </>
  );
}
