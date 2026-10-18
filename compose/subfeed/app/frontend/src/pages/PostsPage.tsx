import { useSearchParams } from "react-router";

import { PostFiltersBar, PostList } from "@/components/feeds/PostList";
import { PageHeader } from "@/components/layout/States";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useFeeds } from "@/lib/queries";
import { usePostFilters } from "@/lib/useFilters";

export function PostsPage() {
  const [filters, setFilters] = usePostFilters(useSearchParams());
  const { data } = useFeeds();
  return (
    <>
      <PageHeader title="All posts" description="What every feed found, newest first." />
      <div className="flex flex-col gap-6">
        <PostFiltersBar filters={filters} onChange={setFilters}>
          <Select value={filters.feed ?? "all"} onValueChange={(feed) => setFilters({ ...filters, feed: feed === "all" ? undefined : feed })}>
            <SelectTrigger className="w-44" aria-label="Which feed"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Every feed</SelectItem>
              {data?.feeds.map((feed) => <SelectItem key={feed.slug} value={feed.slug}>{feed.title}</SelectItem>)}
            </SelectContent>
          </Select>
        </PostFiltersBar>
        <PostList filters={filters} showFeed={!filters.feed} />
      </div>
    </>
  );
}
