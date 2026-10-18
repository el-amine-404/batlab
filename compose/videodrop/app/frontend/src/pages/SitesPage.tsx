import { ChevronRight, ExternalLink, Globe, KeyRound, Search, TriangleAlert } from "lucide-react";
import { memo, useDeferredValue, useMemo, useState } from "react";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Tooltip } from "@/components/ui/tooltip";
import type { Site } from "@/lib/api";
import { useSites } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** A big category shows this many until asked for all. */
const FIRST = 24;

/** A site's icon (asked of videodrop, which keeps it), else its first letter on a colour of its own. */
function Icon({ site }: { site: Site }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    const hue = [...site.domain].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 360;
    return (
      <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-white"
            style={{ background: `oklch(0.45 0.12 ${hue})` }}>
        {site.name.replace(/^[^\p{L}\p{N}]+/u, "").charAt(0).toUpperCase() || "?"}
      </span>
    );
  }
  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-white/90 p-1.5">
      <img src={`/api/v1/sites/${encodeURIComponent(site.domain)}/icon`} alt="" loading="lazy" decoding="async"
           className="size-full object-contain" onError={() => setFailed(true)} />
    </span>
  );
}

const SiteCard = memo(function SiteCard({ site }: { site: Site }) {
  return (
    <li className={cn("flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-3.5 [contain-intrinsic-size:auto_8.5rem] [content-visibility:auto]",
                      site.broken && "opacity-60")}>
      <div className="flex min-w-0 items-center gap-3">
        <Icon site={site} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={site.name}>{site.name}</p>
          <a href={site.url} target="_blank" rel="noreferrer noopener"
             className="inline-flex max-w-full items-center gap-1 text-xs text-sky-300 hover:underline">
            <span className="truncate">{site.domain}</span><ExternalLink className="size-3 shrink-0" />
          </a>
        </div>
      </div>
      <p className="line-clamp-2 text-sm text-muted-foreground" title={site.description}>{site.description}</p>
      <div className="mt-auto flex flex-wrap items-center gap-1.5 text-[11px]">
        {site.kinds.map((kind) => <span key={kind} className="rounded-md bg-secondary px-1.5 py-0.5 text-muted-foreground">{kind}</span>)}
        {site.account && (
          <Tooltip content="Some of it needs a signed-in account: cookies (Settings → Cookies, for YouTube) or a login.">
            <span tabIndex={0} className="inline-flex cursor-help items-center gap-1 rounded-md bg-sky-400/10 px-1.5 py-0.5 text-sky-300">
              <KeyRound className="size-3" /> account
            </span>
          </Tooltip>
        )}
        {site.broken && (
          <Tooltip content="yt-dlp marks it as not working now: downloads from it will probably fail until a release fixes it.">
            <span tabIndex={0} className="inline-flex cursor-help items-center gap-1 rounded-md bg-warning/10 px-1.5 py-0.5 text-warning">
              <TriangleAlert className="size-3" /> broken
            </span>
          </Tooltip>
        )}
        {site.examples.length > 0 && (
          <Tooltip className="max-w-[min(32rem,calc(100vw-2rem))]" content={
            <div className="flex flex-col gap-1">
              <span className="font-medium">Links it takes, for example</span>
              {site.examples.map((url) => <code key={url} className="break-all text-muted-foreground">{url}</code>)}
            </div>
          }>
            <span tabIndex={0} className="ml-auto cursor-help text-muted-foreground underline decoration-dotted underline-offset-4">example links</span>
          </Tooltip>
        )}
      </div>
    </li>
  );
});

/**
 * Where videodrop downloads from: yt-dlp's sites, one card a site, in
 * categories; found by name or domain. Broken ones (yt-dlp says) only when
 * asked for.
 */
export default function SitesPage() {
  const { data, error, isPending, refetch } = useSites();
  const [words, setWords] = useState("");
  const needle = useDeferredValue(words.trim().toLowerCase());
  const [category, setCategory] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  const [all, setAll] = useState<Set<string>>(new Set());

  const shown = useMemo(() => (data?.sites ?? []).filter((site) =>
    (broken || !site.broken)
    && (!needle || site.name.toLowerCase().includes(needle) || site.domain.includes(needle) || site.description.toLowerCase().includes(needle))), [data, needle, broken]);
  const groups = useMemo(() => (data?.categories ?? []).map((c) => ({ ...c, sites: shown.filter((site) => site.category === c.id) }))
    .filter((group) => group.sites.length), [data, shown]);

  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const brokenCount = data.sites.filter((site) => site.broken).length;
  const visible = groups.filter((group) => !category || group.id === category);

  return (
    <>
      <PageHeader title="Sites"
                  description={`Where videodrop downloads from: ${data.sites.length - brokenCount} sites working, as yt-dlp ${data.yt_dlp} knows them. A link from any other site is refused before it is asked for.`} />
      <div className="sticky top-14 z-10 -mx-4 mb-5 flex flex-col gap-3 border-b border-border bg-background/85 px-4 py-3 backdrop-blur lg:top-0">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex h-9 min-w-48 flex-1 items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input value={words} onChange={(event) => setWords(event.target.value)} placeholder="Find a site: name or domain"
                   className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground" />
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={broken} onChange={(event) => setBroken(event.target.checked)} className="accent-sky-400" />
            Show the {brokenCount} broken ones
          </label>
        </div>
        <div className="scroll-thin -mb-1 flex gap-1.5 overflow-x-auto pb-1">
          {[{ id: null, label: "All", count: shown.length }, ...groups.map((g) => ({ id: g.id, label: g.label, count: g.sites.length }))].map((chip) => (
            <button key={chip.id ?? "all"} type="button" onClick={() => setCategory(chip.id)}
                    className={cn("shrink-0 rounded-full border px-3 py-1 text-xs whitespace-nowrap transition-colors",
                                  category === chip.id ? "border-sky-400/50 bg-sky-400/15 text-foreground" : "border-border text-muted-foreground hover:text-foreground")}>
              {chip.label} <span className="tabular-nums opacity-70">{chip.count}</span>
            </button>
          ))}
        </div>
      </div>

      {!visible.length ? (
        <Empty icon={<Globe />} title="No site matches">Try fewer letters, or another category.</Empty>
      ) : visible.map((group) => {
        const whole = needle || category || all.has(group.id) || group.sites.length <= FIRST + 6;
        const sites = whole ? group.sites : group.sites.slice(0, FIRST);
        return (
          <section key={group.id} className="mb-8" aria-labelledby={`sites-${group.id}`}>
            <h2 id={`sites-${group.id}`} className="mb-3 flex items-baseline gap-2 text-lg font-semibold tracking-tight">
              {group.label} <span className="text-sm font-normal text-muted-foreground tabular-nums">{group.sites.length}</span>
            </h2>
            <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {sites.map((site) => <SiteCard key={site.id} site={site} />)}
            </ul>
            {!whole && (
              <button type="button" onClick={() => setAll(new Set(all).add(group.id))}
                      className="mt-3 inline-flex items-center gap-1 text-sm text-sky-300 hover:underline">
                Show all {group.sites.length} <ChevronRight className="size-4" />
              </button>
            )}
          </section>
        );
      })}
    </>
  );
}
