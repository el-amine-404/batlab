import { Braces, LogOut, X } from "lucide-react";
import { Link, NavLink } from "react-router";

import { BROWSE, type Entry, SETTINGS } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useFeeds, useStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";

function EntryLink({ entry, badge }: { entry: Entry; badge?: number }) {
  const Icon = entry.icon;
  return (
    <NavLink to={entry.path} end={entry.path === "/"} className={({ isActive }) => cn(item, isActive && "bg-accent font-medium text-foreground")}>
      <Icon className="size-4 shrink-0" />
      <span className="flex-1 truncate">{entry.label}</span>
      {!!badge && (
        <span className="min-w-5 rounded-full bg-destructive/20 px-1.5 text-center text-[11px] font-medium tabular-nums text-destructive"
              title={`${badge} failing`}>
          {badge}
        </span>
      )}
    </NavLink>
  );
}

export function Sidebar({ onClose }: { onClose: () => void }) {
  const { data: status } = useStatus();
  const { data: feeds } = useFeeds();
  const failing = feeds?.feeds.filter((feed) => feed.enabled && feed.failures > 0).length;
  const me = useMe();
  const can = useCan();
  const shown = (entry: Entry) => !entry.need || can(entry.need);
  return (
    <div className="flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex h-16 items-center justify-between px-5">
        <Link to="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <Logo className="size-8" /> subfeed
        </Link>
        <button type="button" onClick={onClose} aria-label="Close the menu"
                className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent lg:hidden">
          <X className="size-4" />
        </button>
      </div>

      <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 pb-4">
        <div className="flex flex-col gap-0.5">
          {BROWSE.filter(shown).map((entry) => (
            <EntryLink key={entry.path} entry={entry} badge={entry.path === "/" ? failing : undefined} />
          ))}
        </div>
        <p className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">Settings</p>
        <div className="flex flex-col gap-0.5">
          {SETTINGS.filter(shown).map((entry) => (
            <EntryLink key={entry.path} entry={entry} />
          ))}
        </div>
        <p className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">Developers</p>
        <a href="/docs" className={item}>
          <Braces className="size-4 shrink-0" /> <span className="flex-1 truncate">API</span>
        </a>
      </nav>

      <div className="flex items-center gap-2.5 border-t border-border px-3 pt-3">
        <Link to="/settings/account" className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-accent" title="Your account">
          <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/20 text-xs font-semibold text-orange-300">
            {me.name.charAt(0).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{me.name}</span>
            <span className="block truncate text-[11px] text-muted-foreground">{me.permissions.includes("admin") ? "Admin" : "Member"}</span>
          </span>
        </Link>
        <button type="button" onClick={() => void signOut()} aria-label="Sign out" title="Sign out"
                className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
          <LogOut className="size-4" />
        </button>
      </div>
      <div className="px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] text-xs text-muted-foreground/70">
        <Link to="/settings/status" className="hover:text-foreground">
          subfeed {status ? `v${status.version}` : ""}
        </Link>
      </div>
    </div>
  );
}
