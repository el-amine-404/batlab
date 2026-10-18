import { Braces, ChevronDown, Settings, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router";

import { BROWSE, type Entry, SETTINGS } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { type Live, useCounts, useIssues, useStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";

type Tone = "active" | "error" | "warning";
const TONES: Record<Tone, string> = {
  active: "bg-primary/20 text-sky-300",
  error: "bg-destructive/20 text-destructive",
  warning: "bg-warning/20 text-warning",
};

function EntryLink({ entry, badge, tone = "active", title, nested = false }: {
  entry: Entry;
  badge?: number;
  tone?: Tone;
  title?: string;
  nested?: boolean;
}) {
  const Icon = entry.icon;
  return (
    <NavLink to={entry.path} end={entry.path === "/"}
             className={({ isActive }) => cn(item, nested && "h-8 pl-9 text-[13px]", isActive && "bg-accent font-medium text-foreground")}>
      <Icon className={cn("shrink-0", nested ? "size-3.5" : "size-4")} />
      <span className="flex-1 truncate">{entry.label}</span>
      {!!badge && (
        <span className={cn("min-w-5 rounded-full px-1.5 text-center text-[11px] font-medium tabular-nums", TONES[tone])} title={title}>
          {badge}
        </span>
      )}
    </NavLink>
  );
}

const LIVE = {
  live: { dot: "bg-success", label: "Live" },
  connecting: { dot: "bg-warning animate-pulse", label: "Connecting" },
  offline: { dot: "bg-destructive", label: "Offline" },
} as const;

export function Sidebar({ live, onClose }: { live: Live; onClose: () => void }) {
  const { pathname } = useLocation();
  const { data: status } = useStatus();
  const { data: counts } = useCounts();
  const { data: issues } = useIssues();
  const inSettings = pathname.startsWith("/settings");
  // Open while a settings page shows; can be opened from elsewhere too.
  const [open, setOpen] = useState(inSettings);
  useEffect(() => setOpen(inSettings), [inSettings]);

  const active = counts ? counts.queued + counts.running : 0;
  const problems = issues ? issues.counts.error + issues.counts.warning : 0;
  const badges: Record<string, { badge?: number; tone?: Tone; title?: string }> = {
    "/": { badge: active, tone: "active", title: `${active} running or queued` },
    "/issues": { badge: problems, tone: issues?.counts.error ? "error" : "warning", title: `${problems} to look at` },
  };

  return (
    <div className="flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex h-16 items-center justify-between px-5">
        <Link to="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <Logo className="size-8" /> videodrop
        </Link>
        <button type="button" onClick={onClose} aria-label="Close the menu"
                className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent lg:hidden">
          <X className="size-4" />
        </button>
      </div>

      <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 pb-4">
        <div className="flex flex-col gap-0.5">
          {BROWSE.map((entry) => <EntryLink key={entry.path} entry={entry} {...badges[entry.path]} />)}
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="settings-menu"
                  className={cn(item, "w-full", inSettings && "text-foreground")}>
            <Settings className="size-4 shrink-0" />
            <span className="flex-1 truncate text-left">Settings</span>
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
          </button>
          {open && (
            <div id="settings-menu" className="relative flex flex-col gap-0.5">
              <span className="absolute top-1 bottom-1 left-[19px] w-px bg-border" aria-hidden />
              {SETTINGS.map((entry) => <EntryLink key={entry.path} entry={entry} nested />)}
            </div>
          )}
        </div>
        <p className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">Developers</p>
        <a href="/docs" className={item}>
          <Braces className="size-4 shrink-0" /> <span className="flex-1 truncate">API</span>
        </a>
      </nav>

      <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] text-xs text-muted-foreground/70">
        <Link to="/settings/status" className="hover:text-foreground">
          videodrop {status ? `v${status.version}` : ""}
        </Link>
        <span className="flex items-center gap-1.5" title="Whether this page gets the downloads' changes as they happen">
          <span className={cn("size-1.5 rounded-full", LIVE[live].dot)} /> {LIVE[live].label}
        </span>
      </div>
    </div>
  );
}
