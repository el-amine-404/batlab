import { Braces, X } from "lucide-react";
import { Link, NavLink } from "react-router";

import { BROWSE, type Entry, SETTINGS } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { type Live, useCounts, useStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";

function EntryLink({ entry, badge, tone }: { entry: Entry; badge?: number; tone?: "active" | "failed" }) {
  const Icon = entry.icon;
  return (
    <NavLink to={entry.path} end={entry.path === "/"} className={({ isActive }) => cn(item, isActive && "bg-accent font-medium text-foreground")}>
      <Icon className="size-4 shrink-0" />
      <span className="flex-1 truncate">{entry.label}</span>
      {!!badge && (
        <span className={cn("min-w-5 rounded-full px-1.5 text-center text-[11px] font-medium tabular-nums",
                            tone === "failed" ? "bg-destructive/20 text-destructive" : "bg-primary/20 text-sky-300")}
              title={tone === "failed" ? `${badge} failed` : `${badge} running or queued`}>
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
  const { data: status } = useStatus();
  const { data: counts } = useCounts();
  const active = counts ? counts.queued + counts.running : 0;
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
          {BROWSE.map((entry) => (
            <EntryLink key={entry.path} entry={entry} badge={entry.path === "/" ? active : undefined} tone="active" />
          ))}
        </div>
        <p className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">Settings</p>
        <div className="flex flex-col gap-0.5">
          {SETTINGS.map((entry) => (
            <EntryLink key={entry.path} entry={entry} />
          ))}
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
