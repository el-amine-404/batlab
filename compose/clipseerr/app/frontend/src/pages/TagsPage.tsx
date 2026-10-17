import { ArrowDownAZ, CalendarArrowDown, ChevronDown, Eye, History, Tags as TagsIcon, X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Hint } from "@/components/ui/hint";
import { MoreLoader } from "@/components/video/MoreLoader";
import { GridSkeleton, VideoGrid } from "@/components/video/VideoGrid";
import type { Tag, TagSort } from "@/lib/api";
import { useTagged, useTags } from "@/lib/queries";
import { cn } from "@/lib/utils";

const GROUPS: { title: string; noun: [string, string]; has: (tag: Tag) => boolean }[] = [
  { title: "Badges", noun: ["badge", "badges"], has: (tag) => !tag.id.includes(":") },
  { title: "Languages", noun: ["language", "languages"], has: (tag) => tag.id.startsWith("lang:") },
  { title: "Genres", noun: ["genre", "genres"], has: (tag) => tag.id.startsWith("genre:") },
];

const SORTS: [TagSort, typeof History, string][] = [
  ["seen", History, "Latest seen"],
  ["newest", CalendarArrowDown, "Newest out"],
  ["views", Eye, "Most viewed"],
  ["title", ArrowDownAZ, "A to Z"],
];

function Chip({ tag, on, toggle }: { tag: Tag; on: boolean; toggle: () => void }) {
  return (
    <Hint text={tag.hint}>
      <button type="button" onClick={toggle} aria-pressed={on}
              className={cn("flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-sm transition",
                            on ? "border-rose-400/50 bg-rose-400/15 text-rose-100"
                               : "border-border bg-secondary/50 text-foreground/85 hover:border-rose-400/30 hover:bg-accent")}>
        {tag.label}
        <span className="text-xs tabular-nums text-muted-foreground">{tag.count}</span>
        {on && <X className="size-3.5" aria-label="Remove" />}
      </button>
    </Hint>
  );
}

/**
 * One kind of tag: a single row of the most used, "Show all" opens the rest
 * here; the chosen ones are always in the row, so they can be taken away.
 */
function Group({ title, noun, tags, chosen, toggle }: {
  title: string;
  noun: [string, string];
  tags: Tag[];
  chosen: string[];
  toggle: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const row = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  // Chosen first, then the most used: what's in the closed row matters most.
  const ordered = [...tags.filter((t) => chosen.includes(t.id)), ...tags.filter((t) => !chosen.includes(t.id))];

  useLayoutEffect(() => {
    const el = row.current;
    if (!el) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 4);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [tags.length]);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <p className="text-[11px] font-medium tracking-wider text-muted-foreground/80 uppercase">
          {title} <span className="normal-case tracking-normal text-muted-foreground/60">· {tags.length} {noun[tags.length === 1 ? 0 : 1]}</span>
        </p>
        {(overflows || open) && (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                  className="flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-rose-300 hover:bg-accent">
            {open ? "Show fewer" : `Show all ${tags.length}`} <ChevronDown className={cn("size-3.5 transition", open && "rotate-180")} />
          </button>
        )}
      </div>
      <div ref={row} className={cn("flex flex-wrap gap-1.5", !open && "max-h-8 overflow-hidden")}>
        {ordered.map((tag) => <Chip key={tag.id} tag={tag} on={chosen.includes(tag.id)} toggle={() => toggle(tag.id)} />)}
      </div>
    </div>
  );
}

const list = (labels: string[]) =>
  labels.length < 2 ? labels.join("") : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;

/**
 * /tags?tag=explicit&tag=lang:el&sort=newest: the videos clipseerr knows that
 * carry every tag chosen. A badge anywhere leads here with its tag; more can
 * be added, and the address keeps the choice, so a link opens the same list.
 */
export function TagsPage() {
  const [params, setParams] = useSearchParams();
  const chosen = params.getAll("tag");
  const sort = (SORTS.find(([value]) => value === params.get("sort"))?.[0] ?? "seen") as TagSort;
  const tags = useTags();
  const found = useTagged(chosen, sort);
  const known = new Map((tags.data?.tags ?? []).map((tag) => [tag.id, tag]));

  const update = (next: string[], nextSort = sort) => {
    const query = new URLSearchParams(next.map((t) => ["tag", t]));
    if (nextSort !== "seen") query.set("sort", nextSort);
    setParams(query, { replace: true });
  };
  const toggle = (id: string) => update(chosen.includes(id) ? chosen.filter((t) => t !== id) : [...chosen, id]);

  const items = found.data?.pages.flatMap((page) => page.items) ?? [];
  const total = found.data?.pages[found.data.pages.length - 1]?.total ?? 0;
  const labels = chosen.map((id) => known.get(id)?.label ?? id);
  const one = chosen.length === 1 ? known.get(chosen[0]) : undefined;

  return (
    <>
      {/* The tags chosen are highlighted below: the title doesn't list them again. */}
      <PageHeader
        eyebrow="Tags"
        title={one ? one.label : chosen.length > 1 ? `${chosen.length} tags` : "Videos by tag"}
        description={one ? one.hint
          : chosen.length > 1 ? `Videos that are all of: ${list(labels)}.`
            : "Pick a tag, or several: the videos clipseerr has shown that carry them all."}
        actions={chosen.length > 0 && (
          <button type="button" onClick={() => update([])} className="rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground">
            Clear
          </button>
        )}
      />

      {tags.isPending ? <Loading /> : tags.error ? <Failed error={tags.error} retry={() => void tags.refetch()} /> : (
        <div className="mb-8 flex flex-col gap-4">
          {GROUPS.map((group) => {
            const inGroup = (tags.data?.tags ?? []).filter(group.has);
            return inGroup.length ? (
              <Group key={group.title} title={group.title} noun={group.noun} tags={inGroup} chosen={chosen} toggle={toggle} />
            ) : null;
          })}
        </div>
      )}

      {!chosen.length ? (
        <Empty icon={<TagsIcon />} title="No tag chosen">A badge on any card or video page brings you here with its tag.</Empty>
      ) : found.error ? (
        <Failed error={found.error} retry={() => void found.refetch()} />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">{found.isPending ? " " : `${total} ${total === 1 ? "video" : "videos"}`}</p>
            <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="Order">
              {SORTS.map(([value, Icon, label]) => (
                <Hint key={value} text={label}>
                  <button type="button" aria-label={label} aria-pressed={sort === value} onClick={() => update(chosen, value)}
                          className={cn("flex size-8 items-center justify-center rounded-md text-muted-foreground",
                                        sort === value && "bg-accent text-foreground")}>
                    <Icon className="size-4" />
                  </button>
                </Hint>
              ))}
            </div>
          </div>
          {found.isPending ? <GridSkeleton /> : !items.length ? (
            <Empty icon={<TagsIcon />} title="No video carries all of these">Take a tag away to see more.</Empty>
          ) : (
            <>
              <VideoGrid videos={items} />
              <MoreLoader shown={items.length} total={total} hasMore={!!found.hasNextPage} loading={found.isFetchingNextPage}
                          failed={found.isFetchNextPageError} load={() => void found.fetchNextPage()} />
            </>
          )}
        </>
      )}
    </>
  );
}
