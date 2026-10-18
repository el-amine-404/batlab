import { AlertOctagon, AlertTriangle, ArrowRight, ChevronDown, CircleCheck, Info, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Card } from "@/components/ui/card";
import type { Issue } from "@/lib/api";
import { useIssues } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

type Level = Issue["level"];

const LEVELS: Record<Level, { icon: LucideIcon; tone: string; bar: string; label: string }> = {
  error: { icon: AlertOctagon, tone: "text-destructive", bar: "bg-destructive", label: "Needs fixing" },
  warning: { icon: AlertTriangle, tone: "text-warning", bar: "bg-warning", label: "Worth a look" },
  info: { icon: Info, tone: "text-sky-300", bar: "bg-sky-400", label: "For your information" },
};
const ORDER: Level[] = ["error", "warning", "info"];

/** One issue: a line (what, since when); opened, why it matters, what it is about, and where to fix it. */
function Row({ issue, open, onToggle }: { issue: Issue; open: boolean; onToggle: () => void }) {
  const level = LEVELS[issue.level];
  const Icon = level.icon;
  return (
    <li className="relative">
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-0.5", level.bar, !open && "opacity-50")} />
      <button type="button" onClick={onToggle} aria-expanded={open}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.03] focus-visible:bg-white/[0.03] focus-visible:outline-none">
        <Icon className={cn("size-4 shrink-0", level.tone)} />
        <span className="min-w-0 flex-1 truncate font-medium">{issue.title}</span>
        {issue.items.length > 0 && <span className="shrink-0 rounded-full bg-secondary px-2 text-xs text-muted-foreground tabular-nums">{issue.items.length}</span>}
        {issue.since && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{timeAgo(issue.since)}</span>}
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="flex flex-col gap-3 px-4 pb-4 pl-11 text-sm">
          <p className="text-muted-foreground">{issue.detail}</p>
          {issue.items.length > 0 && (
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {issue.items.map((item) => (
                <li key={item.path}>
                  <Link to={item.path} className="group flex items-center gap-3 px-3 py-2 hover:bg-white/[0.03]">
                    <div className="min-w-0 flex-1">
                      <p className="truncate group-hover:underline">{item.label}</p>
                      {item.detail && <p className="line-clamp-2 text-xs text-muted-foreground">{item.detail}</p>}
                    </div>
                    <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {issue.action && (
            <Link to={issue.action.path} className="inline-flex items-center gap-1 self-start text-sky-300 hover:underline">
              {issue.action.label} <ArrowRight className="size-3.5" />
            </Link>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * What needs a person's eye, as many as there are: a count of each level
 * (which also filters), then the issues grouped by level, a line each;
 * a line opens on what it is about. Errors are open to start with.
 */
export default function IssuesPage() {
  const { data, error, isPending, refetch } = useIssues();
  const [only, setOnly] = useState<Level | null>(null);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const few = data.issues.length <= 2;
  const isOpen = (issue: Issue) => opened[issue.id] ?? (few || issue.level === "error");

  return (
    <>
      <PageHeader title="Issues" description="What needs your eye: each one says what is wrong, why it matters, what it is about and where to fix it. It goes away once fixed." />
      {!data.issues.length ? (
        <Empty icon={<CircleCheck />} title="Nothing to fix">yt-dlp is current, the disk has room, and downloads go through.</Empty>
      ) : (
        <>
          <div className="mb-4 grid grid-cols-3 gap-2">
            {ORDER.map((level) => {
              const { icon: Icon, tone, label } = LEVELS[level];
              const count = data.counts[level];
              return (
                <button key={level} type="button" disabled={!count} onClick={() => setOnly(only === level ? null : level)} aria-pressed={only === level}
                        className={cn("flex items-center gap-3 rounded-xl border border-border bg-card p-3 text-left transition-colors enabled:hover:border-white/20 disabled:opacity-50",
                                      only === level && "border-white/30 bg-white/[0.04]")}>
                  <Icon className={cn("size-5 shrink-0", count ? tone : "text-muted-foreground")} />
                  <div className="min-w-0">
                    <p className="text-xl leading-none font-semibold tabular-nums">{count}</p>
                    <p className="mt-1 truncate text-xs text-muted-foreground">{label}</p>
                  </div>
                </button>
              );
            })}
          </div>
          {ORDER.filter((level) => (!only || only === level) && data.counts[level]).map((level) => (
            <section key={level} className="mb-4">
              <h2 className="mb-2 text-[11px] font-medium tracking-wider text-muted-foreground uppercase">{LEVELS[level].label}</h2>
              <Card className="gap-0 overflow-hidden p-0">
                <ul className="divide-y divide-border">
                  {data.issues.filter((issue) => issue.level === level).map((issue) => (
                    <Row key={issue.id} issue={issue} open={isOpen(issue)} onToggle={() => setOpened({ ...opened, [issue.id]: !isOpen(issue) })} />
                  ))}
                </ul>
              </Card>
            </section>
          ))}
        </>
      )}
    </>
  );
}
