import type { LucideIcon } from "lucide-react";
import { type ReactNode, Suspense, useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { cn } from "./cn";

export type SettingsEntry = { to: string; label: string; icon: LucideIcon };

/**
 * The settings' own list beside their page: a column on a computer, a row of
 * tabs that scrolls on a phone. The sidebar keeps one Settings entry, however
 * many there are. entries: only the ones this person may open.
 */
export function SettingsLayout({ entries, loading }: { entries: SettingsEntry[]; loading: ReactNode }) {
  const { pathname } = useLocation();
  const list = useRef<HTMLElement>(null);

  // On a phone the open tab may be off the row's edge: bring it into view.
  useEffect(() => {
    list.current?.querySelector<HTMLElement>("[aria-current=page]")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname]);

  return (
    <div className="lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-10">
      <nav ref={list} aria-label="Settings"
           className="-mx-4 mb-6 flex gap-1 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:-mx-6 sm:px-6 lg:mx-0 lg:mb-0 lg:flex-col lg:self-start lg:overflow-visible lg:p-0">
        <p className="mb-1.5 hidden px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase lg:block">Settings</p>
        {entries.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to}
                   className={({ isActive }) => cn(
                     "flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-3 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                     "max-lg:border max-lg:border-border",
                     isActive && "bg-accent font-medium text-foreground max-lg:border-transparent",
                   )}>
            <Icon className="size-4 shrink-0" /> {label}
          </NavLink>
        ))}
      </nav>
      <div className="min-w-0">
        <Suspense fallback={loading}>
          <Outlet />
        </Suspense>
      </div>
    </div>
  );
}
