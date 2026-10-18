import { AlertOctagon, AlertTriangle, ArrowRight, CircleCheck, Info, type LucideIcon } from "lucide-react";
import { Link } from "react-router";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import type { Issue } from "@/lib/api";
import { useIssues } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

const LEVELS: Record<Issue["level"], { icon: LucideIcon; tone: string; label: string }> = {
  error: { icon: AlertOctagon, tone: "border-destructive/30 bg-destructive/5 text-destructive", label: "Needs fixing" },
  warning: { icon: AlertTriangle, tone: "border-warning/30 bg-warning/5 text-warning", label: "Worth a look" },
  info: { icon: Info, tone: "border-sky-400/25 bg-sky-400/5 text-sky-300", label: "For your information" },
};

export default function IssuesPage() {
  const { data, error, isPending, refetch } = useIssues();
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  return (
    <>
      <PageHeader title="Issues" description="What needs your eye: each one says what is wrong, why it matters and where to fix it. It goes away once fixed." />
      {!data.issues.length ? (
        <Empty icon={<CircleCheck />} title="Nothing to fix">yt-dlp is current, the disk has room, and downloads go through.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.issues.map((issue) => {
            const level = LEVELS[issue.level];
            const Icon = level.icon;
            return (
              <li key={issue.id} className={cn("flex gap-3 rounded-xl border p-4", level.tone)}>
                <Icon className="mt-0.5 size-5 shrink-0" />
                <div className="min-w-0 flex-1 text-foreground">
                  <p className="text-[11px] font-medium tracking-wider uppercase opacity-70">{level.label}{issue.since ? ` · ${timeAgo(issue.since)}` : ""}</p>
                  <p className="font-medium">{issue.title}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{issue.detail}</p>
                  {issue.action && (
                    <Link to={issue.action.path} className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:underline">
                      {issue.action.label} <ArrowRight className="size-3.5" />
                    </Link>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
