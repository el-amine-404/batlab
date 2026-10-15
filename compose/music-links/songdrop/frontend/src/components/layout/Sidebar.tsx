import { BookOpen, Braces, X } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router";

import { type Counter, HOME, type Screen, SECTIONS } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { useJobs } from "@/lib/jobs";
import { cn } from "@/lib/utils";

const item =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground";

function useCounts(): Record<Counter, number> {
  const { queue, missing } = useJobs();
  return { queue: queue.length, missing: missing.length };
}

function ScreenLink({ screen, nested = false, count }: { screen: Screen; nested?: boolean; count?: number }) {
  const Icon = screen.icon;
  return (
    <NavLink
      to={screen.path}
      end
      className={({ isActive }) =>
        cn(item, nested && "h-8 pl-10 text-[13px]", isActive && "bg-accent font-medium text-foreground")
      }
    >
      {!nested && <Icon className="size-4 shrink-0" />}
      <span className="flex-1 truncate">{screen.label}</span>
      {!!count && (
        <span className="min-w-5 rounded-full bg-primary/20 px-1.5 text-center text-[11px] font-medium tabular-nums text-violet-200">
          {count}
        </span>
      )}
    </NavLink>
  );
}

/** Made from app/navigation.tsx. A section shows its screens while one of them is open. */
export function Sidebar({ onClose }: { onClose: () => void }) {
  const { pathname } = useLocation();
  const counts = useCounts();

  return (
    <div className="flex h-full flex-col pt-[env(safe-area-inset-top)]">
      <div className="flex h-16 items-center justify-between px-5">
        <Link to="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <Logo className="size-8" /> songdrop
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
        <ScreenLink screen={HOME} />
        {SECTIONS.map((section) => {
          const open = pathname.startsWith(section.path);
          const Icon = section.icon;
          const total = section.screens.reduce((sum, screen) => sum + (screen.counter ? counts[screen.counter] : 0), 0);
          return (
            <div key={section.path} className="mt-1">
              <Link to={section.path} className={cn(item, open && "text-foreground")}>
                <Icon className="size-4 shrink-0" />
                <span className="flex-1">{section.label}</span>
                {!open && !!total && <span className="size-2 rounded-full bg-primary" aria-label={`${total} to see`} />}
              </Link>
              {open && (
                <div className="mt-0.5 flex flex-col gap-0.5">
                  {section.screens.map((screen) => (
                    <ScreenLink
                      key={screen.path}
                      screen={screen}
                      nested
                      count={screen.counter ? counts[screen.counter] : undefined}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="border-t border-border p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <a href="/docs/" className={item}>
          <BookOpen className="size-4" /> Docs
        </a>
        <a href="/docs/api/" className={item}>
          <Braces className="size-4" /> API
        </a>
      </div>
    </div>
  );
}
