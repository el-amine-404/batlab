import { Braces, X } from "lucide-react";
import { Link, NavLink } from "react-router";

import { BROWSE, type Entry, SETTINGS } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { useActiveCount } from "@/lib/queries";
import { cn } from "@/lib/utils";

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";

function EntryLink({ entry, count }: { entry: Entry; count?: number }) {
  const Icon = entry.icon;
  return (
    <NavLink
      to={entry.path}
      end={entry.path === "/"}
      className={({ isActive }) => cn(item, isActive && "bg-accent font-medium text-foreground")}
    >
      <Icon className="size-4 shrink-0" />
      <span className="flex-1 truncate">{entry.label}</span>
      {!!count && (
        <span className="min-w-5 rounded-full bg-primary/20 px-1.5 text-center text-[11px] font-medium tabular-nums text-rose-200">
          {count}
        </span>
      )}
    </NavLink>
  );
}

export function Sidebar({ onClose }: { onClose: () => void }) {
  const { data: active } = useActiveCount();
  return (
    <div className="flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex h-16 items-center justify-between px-5">
        <Link to="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <Logo className="size-8" /> clipseerr
        </Link>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the menu"
          className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent lg:hidden"
        >
          <X className="size-4" />
        </button>
      </div>

      <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 pb-4">
        <div className="flex flex-col gap-0.5">
          {BROWSE.map((entry) => (
            <EntryLink key={entry.path} entry={entry} count={entry.counter ? active : undefined} />
          ))}
        </div>
        <p className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">Settings</p>
        <div className="flex flex-col gap-0.5">
          {SETTINGS.map((entry) => (
            <EntryLink key={entry.path} entry={entry} />
          ))}
        </div>
      </nav>

      <div className="border-t border-border p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <a href="/api/docs" className={item}>
          <Braces className="size-4" /> API
        </a>
      </div>
    </div>
  );
}
