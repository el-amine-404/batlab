import { ExternalLink, FileText, Film, Image, Images, Link2, Loader2, Search, Video } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { Empty, Failed } from "@/components/layout/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { LinkKind, Post, PostFilters } from "@/lib/api";
import { usePosts } from "@/lib/queries";
import { cn, timeAgo, ytImage } from "@/lib/utils";

const KINDS: Record<LinkKind, { label: string; icon: ReactNode }> = {
  video: { label: "Videos", icon: <Video /> },
  reddit_video: { label: "Reddit videos", icon: <Film /> },
  image: { label: "Images", icon: <Image /> },
  gallery: { label: "Galleries", icon: <Images /> },
  text: { label: "Text", icon: <FileText /> },
  other: { label: "Links", icon: <Link2 /> },
};

/** The filters bar: words, what posts link to, one post a video. */
export function PostFiltersBar({ filters, onChange, children }: {
  filters: PostFilters;
  onChange: (filters: PostFilters) => void;
  children?: ReactNode;
}) {
  const [words, setWords] = useState(filters.q ?? "");
  // Typing settles for a moment before the list is asked again.
  useEffect(() => {
    const timer = setTimeout(() => {
      if ((filters.q ?? "") !== words.trim()) onChange({ ...filters, q: words.trim() || undefined });
    }, 300);
    return () => clearTimeout(timer);
  }, [words, filters, onChange]);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-48 flex-1">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={words} onChange={(e) => setWords(e.target.value)} placeholder="Words of the title" className="pl-9" aria-label="Search the titles" />
      </div>
      {children}
      <Select value={filters.link ?? "all"} onValueChange={(value) => onChange({ ...filters, link: value === "all" ? undefined : (value as LinkKind) })}>
        <SelectTrigger className="w-40" aria-label="What the posts link to"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All posts</SelectItem>
          {Object.entries(KINDS).map(([kind, { label }]) => <SelectItem key={kind} value={kind}>{label}</SelectItem>)}
        </SelectContent>
      </Select>
      <div className="flex items-center gap-2">
        <Switch id="unique" checked={!!filters.unique} onCheckedChange={(unique) => onChange({ ...filters, unique })} />
        <Label htmlFor="unique" className="text-sm font-normal text-muted-foreground" title="A video posted again shows once: its first post">
          One post a video
        </Label>
      </div>
    </div>
  );
}

function PostRow({ post, showFeed }: { post: Post; showFeed: boolean }) {
  const video = post.videos[0];
  const kind = KINDS[post.link.kind];
  return (
    <li className="flex gap-3 rounded-xl border border-border bg-card p-3">
      {video?.provider === "youtube" ? (
        <a href={video.url} target="_blank" rel="noreferrer" className="shrink-0" aria-label="Watch on YouTube">
          <img src={ytImage(video.id)} alt="" loading="lazy" className="aspect-video w-28 rounded-md object-cover sm:w-36" />
        </a>
      ) : (
        <span className="flex aspect-video w-28 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground sm:w-36 [&_svg]:size-5">
          {kind.icon}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <a href={post.url} target="_blank" rel="noreferrer" className="line-clamp-2 font-medium hover:underline">{post.title}</a>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span title={new Date(post.posted_at * 1000).toLocaleString()}>{timeAgo(post.posted_at)}</span>
          {post.author && <span>· u/{post.author}</span>}
          {showFeed && <span>· {post.feed}</span>}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge variant={post.link.kind === "video" ? "youtube" : "muted"}>{kind.icon} {kind.label.replace(/s$/, "")}</Badge>
          {post.link.url && (
            <a href={post.link.url} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 truncate text-xs text-orange-300 hover:underline">
              {post.link.domain} <ExternalLink className="size-3 shrink-0" />
            </a>
          )}
          {post.videos.length > 1 && <span className="text-xs text-muted-foreground">+{post.videos.length - 1} more videos</span>}
        </div>
      </div>
    </li>
  );
}

/** The posts the filters give, newest first, a page at a time. */
export function PostList({ filters, showFeed = false }: { filters: PostFilters; showFeed?: boolean }) {
  const { data, error, isPending, isFetching, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = usePosts(filters);
  if (isPending) {
    return (
      <ul className="flex flex-col gap-2" aria-busy>
        {Array.from({ length: 6 }, (_, i) => <li key={i} className="skeleton h-24 rounded-xl" />)}
      </ul>
    );
  }
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const posts = data.pages.flatMap((page) => page.posts);
  const total = data.pages[0]?.total ?? 0;
  if (!posts.length) {
    return (
      <Empty icon={<Search />} title="No post here">
        {filters.q || filters.link || filters.unique ? "Nothing matches these filters." : "The feed hasn't found any post yet."}
      </Empty>
    );
  }
  return (
    <div className={cn("flex flex-col gap-3 transition-opacity", isFetching && !isFetchingNextPage && "opacity-70")}>
      <p className="text-xs text-muted-foreground">{total.toLocaleString()} post{total === 1 ? "" : "s"}</p>
      <ul className="flex flex-col gap-2">
        {posts.map((post) => <PostRow key={post.id} post={post} showFeed={showFeed} />)}
      </ul>
      {hasNextPage && (
        <Button variant="secondary" className="self-center" onClick={() => void fetchNextPage()} disabled={isFetchingNextPage}>
          {isFetchingNextPage && <Loader2 className="animate-spin" />} More
        </Button>
      )}
    </div>
  );
}
