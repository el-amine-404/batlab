import { Pencil, RotateCw, Rss, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { FeedAddresses } from "@/components/feeds/FeedAddresses";
import { FeedDialog } from "@/components/feeds/FeedDialog";
import { FeedBadge, LastRead } from "@/components/feeds/FeedStatus";
import { PostFiltersBar, PostList } from "@/components/feeds/PostList";
import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { api, errorText } from "@/lib/api";
import { useFeed, useFeedsChanged } from "@/lib/queries";
import { usePostFilters } from "@/lib/useFilters";

export function FeedPage() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const { feed, error, isPending, refetch } = useFeed(slug);
  const changed = useFeedsChanged();
  const [filters, setFilters] = usePostFilters(useSearchParams());
  const [editing, setEditing] = useState(false);

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  if (!feed) {
    return (
      <Empty icon={<Rss />} title="There's no such feed">
        <Link to="/" className="text-orange-300 hover:underline">Back to the feeds</Link>
      </Empty>
    );
  }

  const run = async (work: () => Promise<unknown>, done: string) => {
    try {
      await work();
      toast.success(done);
    } catch (caught) {
      toast.error(errorText(caught));
    }
    changed();
  };
  const remove = async () => {
    if (!window.confirm(`Remove ${feed.title} and its ${feed.posts} posts? Programs reading it get nothing after that.`)) return;
    await run(() => api.deleteFeed(feed.slug), "Removed");
    navigate("/");
  };

  return (
    <>
      <PageHeader
        eyebrow={<span className="flex items-center gap-2">Feed <FeedBadge feed={feed} /></span>}
        title={feed.title}
        description={feed.home_url && <a href={feed.home_url} target="_blank" rel="noreferrer" className="text-orange-300 hover:underline">r/{feed.target}</a>}
        actions={
          <>
            <Button variant="secondary" size="sm" disabled={!feed.enabled} onClick={() => void run(() => api.refreshFeed(feed.slug), "Reading it now")}>
              <RotateCw /> Read now
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}><Pencil /> Change</Button>
            <Button variant="destructive" size="sm" onClick={() => void remove()}><Trash2 /> Remove</Button>
          </>
        }
      />
      <LastRead feed={feed} className="-mt-3 mb-6" />
      <div className="flex flex-col gap-6">
        <FeedAddresses slug={feed.slug} filters={filters} />
        <PostFiltersBar filters={filters} onChange={setFilters} />
        <PostList filters={{ ...filters, feed: feed.slug }} />
      </div>
      <FeedDialog saved={feed} open={editing} onOpenChange={setEditing} />
    </>
  );
}
