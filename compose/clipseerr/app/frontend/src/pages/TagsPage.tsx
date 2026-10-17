import { Tags as TagsIcon, X } from "lucide-react";
import { useSearchParams } from "react-router";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Hint } from "@/components/ui/hint";
import { MoreLoader } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import type { Tag } from "@/lib/api";
import { useTagged, useTags } from "@/lib/queries";
import { cn } from "@/lib/utils";

const GROUPS: { title: string; has: (tag: Tag) => boolean }[] = [
  { title: "Badges", has: (tag) => !tag.id.includes(":") },
  { title: "Languages", has: (tag) => tag.id.startsWith("lang:") },
  { title: "Genres", has: (tag) => tag.id.startsWith("genre:") },
];

/**
 * /tags?tag=explicit&tag=lang:el: the videos clipseerr knows that carry every
 * tag chosen. A badge anywhere leads here with its tag; more can be added,
 * and the address keeps the choice, so a link opens the same list.
 */
export function TagsPage() {
  const [params, setParams] = useSearchParams();
  const chosen = params.getAll("tag");
  const tags = useTags();
  const list = useTagged(chosen);
  const known = new Map((tags.data?.tags ?? []).map((tag) => [tag.id, tag]));

  const toggle = (id: string) => {
    const next = chosen.includes(id) ? chosen.filter((t) => t !== id) : [...chosen, id];
    setParams(new URLSearchParams(next.map((t) => ["tag", t])), { replace: true });
  };

  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const total = list.data?.pages[list.data.pages.length - 1]?.total ?? 0;
  const labels = chosen.map((id) => known.get(id)?.label ?? id);

  return (
    <>
      <PageHeader
        eyebrow="Tags"
        title={labels.length ? labels.join(" · ") : "Videos by tag"}
        description={chosen.length === 1 && known.get(chosen[0])
          ? known.get(chosen[0])!.hint
          : chosen.length > 1 ? "Videos carrying every tag chosen." : "Pick a tag, or several: the videos clipseerr has shown that carry them all."}
      />

      {tags.isPending ? <Loading /> : tags.error ? <Failed error={tags.error} retry={() => void tags.refetch()} /> : (
        <div className="mb-8 flex flex-col gap-4">
          {GROUPS.map((group) => {
            const inGroup = (tags.data?.tags ?? []).filter(group.has);
            if (!inGroup.length) return null;
            return (
              <div key={group.title}>
                <p className="mb-1.5 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">{group.title}</p>
                <div className="flex flex-wrap gap-1.5">
                  {inGroup.map((tag) => {
                    const on = chosen.includes(tag.id);
                    return (
                      <Hint key={tag.id} text={tag.hint}>
                        <button type="button" onClick={() => toggle(tag.id)} aria-pressed={on}
                                className={cn("flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm transition",
                                              on ? "border-rose-400/50 bg-rose-400/15 text-rose-100"
                                                 : "border-border bg-secondary/50 text-foreground/85 hover:border-rose-400/30 hover:bg-accent")}>
                          {tag.label}
                          <span className="text-xs tabular-nums text-muted-foreground">{tag.count}</span>
                          {on && <X className="size-3.5" aria-label="Remove" />}
                        </button>
                      </Hint>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!chosen.length ? (
        <Empty icon={<TagsIcon />} title="No tag chosen">A badge on any card or video page brings you here with its tag.</Empty>
      ) : list.error ? (
        <Failed error={list.error} retry={() => void list.refetch()} />
      ) : list.isPending ? (
        <GridSkeleton />
      ) : !items.length ? (
        <Empty icon={<TagsIcon />} title="No video carries all of these">Take a tag away to see more.</Empty>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted-foreground">{total} {total === 1 ? "video" : "videos"}</p>
          <VideoGrid videos={items} />
          <MoreLoader shown={items.length} total={total} hasMore={!!list.hasNextPage} loading={list.isFetchingNextPage}
                      failed={list.isFetchNextPageError} load={() => void list.fetchNextPage()} />
        </>
      )}
    </>
  );
}
