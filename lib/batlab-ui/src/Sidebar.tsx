import { LogOut, Settings, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";

import { cn } from "./cn";

/** A page of the app in the sidebar, with an optional count beside it. */
export type NavItem = { to: string; label: string; icon: LucideIcon; badge?: Badge };

/** active: work going on (the app's colour); error, warning: something to look at. */
export type Badge = { count: number; tone?: "active" | "error" | "warning"; title?: string };

/** A page outside the app (the docs, the API reference): a plain link, at the bottom. */
export type ExternalLink = { href: string; label: string; icon: LucideIcon };

/** Whether the page gets the app's changes as they happen (videodrop's job stream). */
export type Live = "connecting" | "live" | "offline";

export type SidebarProps = {
  name: string;
  logo: ReactNode;
  items: NavItem[];
  /** Where Settings opens; left out, the app has no Settings entry. */
  settings?: string;
  links?: ExternalLink[];
  user: { name: string; role: string };
  /** Where "Your account" is; the name and avatar link there. */
  account?: string;
  onSignOut: () => void;
  version?: string;
  versionTitle?: string;
  /** The status page the version links to. */
  status?: string;
  live?: Live;
  onClose: () => void;
};

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";
const current = "bg-accent font-medium text-foreground";

const TONES = {
  active: "bg-primary/20 text-primary",
  error: "bg-destructive/20 text-destructive",
  warning: "bg-warning/20 text-warning",
} as const;

const LIVE = {
  live: { dot: "bg-success", label: "Live" },
  connecting: { dot: "bg-warning animate-pulse", label: "Connecting" },
  offline: { dot: "bg-destructive", label: "Offline" },
} as const;

function Count({ badge }: { badge?: Badge }) {
  if (!badge?.count) return null;
  return (
    <span className={cn("min-w-5 rounded-full px-1.5 text-center text-[11px] font-medium tabular-nums", TONES[badge.tone ?? "active"])}
          title={badge.title}>
      {badge.count}
    </span>
  );
}

/**
 * The same sidebar in every app: its pages, one Settings entry (the settings'
 * own list is on their page, SettingsLayout), then the docs and API links, who
 * is signed in, and the version.
 */
export function Sidebar(props: SidebarProps) {
  const { pathname } = useLocation();
  const inSettings = !!props.settings && pathname.startsWith(props.settings);
  const live = props.live && LIVE[props.live];

  return (
    <div className="flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex h-16 items-center justify-between px-5">
        <Link to="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          {props.logo} {props.name}
        </Link>
        <button type="button" onClick={props.onClose} aria-label="Close the menu"
                className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent lg:hidden">
          <X className="size-4" />
        </button>
      </div>

      <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-4">
        {props.items.map(({ to, label, icon: Icon, badge }) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => cn(item, isActive && current)}>
            <Icon className="size-4 shrink-0" />
            <span className="flex-1 truncate">{label}</span>
            <Count badge={badge} />
          </NavLink>
        ))}
        {props.settings && (
          <Link to={props.settings} className={cn(item, "mt-4", inSettings && current)} aria-current={inSettings ? "page" : undefined}>
            <Settings className="size-4 shrink-0" />
            <span className="flex-1 truncate">Settings</span>
          </Link>
        )}
      </nav>

      {!!props.links?.length && (
        <div className="flex flex-col gap-0.5 border-t border-border p-3">
          {props.links.map(({ href, label, icon: Icon }) => (
            <a key={href} href={href} className={item}>
              <Icon className="size-4 shrink-0" /> <span className="flex-1 truncate">{label}</span>
            </a>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2.5 border-t border-border px-3 pt-3">
        <Link to={props.account ?? "/settings/account"} title="Your account"
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-accent">
          <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/20 text-xs font-semibold text-primary">
            {props.user.name.charAt(0).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{props.user.name}</span>
            <span className="block truncate text-[11px] text-muted-foreground">{props.user.role}</span>
          </span>
        </Link>
        <button type="button" onClick={props.onSignOut} aria-label="Sign out" title="Sign out"
                className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
          <LogOut className="size-4" />
        </button>
      </div>
      <div className="flex items-center justify-between gap-2 px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] text-xs text-muted-foreground/70">
        {props.status ? (
          <Link to={props.status} className="hover:text-foreground" title={props.versionTitle ?? "Status"}>
            {props.name} {props.version && `v${props.version}`}
          </Link>
        ) : (
          <span>{props.name} {props.version && `v${props.version}`}</span>
        )}
        {live && (
          <span className="flex items-center gap-1.5" title="Whether this page gets the changes as they happen">
            <span className={cn("size-1.5 rounded-full", live.dot)} /> {live.label}
          </span>
        )}
      </div>
    </div>
  );
}
