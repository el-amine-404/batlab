import { Menu } from "lucide-react";
import { type ReactNode, Suspense, useEffect, useState } from "react";
import { Link, Outlet, ScrollRestoration, useLocation } from "react-router";
import { Toaster } from "sonner";

import { cn } from "./cn";

export type ShellProps = {
  name: string;
  /** The logo in the phone's bar, sized there. */
  logo: ReactNode;
  /** The sidebar; close() shuts the phone's drawer. */
  sidebar: (close: () => void) => ReactNode;
  /** A bar over every screen (clipseerr's search); without it, the bar shows on a phone only. */
  header?: ReactNode;
  /** The screen's width; can follow the path (clipseerr's lists are wider than its settings). */
  width?: string | ((pathname: string) => string);
  /** Shown while a screen's code loads. */
  loading: ReactNode;
  /** Handed to the screens (useOutletContext): the PDF Gateway's signed-in person. */
  context?: unknown;
};

/**
 * The frame of every screen: the sidebar (a drawer on a phone, opened from the
 * bar) and the screen. Back finds a page where it was left; a new page opens
 * at the top.
 */
export function Shell({ name, logo, sidebar, header, width = "max-w-5xl", loading, context }: ShellProps) {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => setOpen(false), [pathname]);

  const menu = (
    <button type="button" onClick={() => setOpen(true)} aria-label="Open the menu"
            className="flex size-10 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden">
      <Menu className="size-5" />
    </button>
  );

  return (
    <>
      <div className="backdrop" aria-hidden />

      {open && <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-64 -translate-x-full border-r border-border bg-background/95 backdrop-blur-xl transition-transform lg:translate-x-0 lg:bg-background/60",
          open && "translate-x-0",
        )}
      >
        {sidebar(() => setOpen(false))}
      </aside>

      <div className="lg:ml-64">
        <header className={cn("sticky top-0 z-30 border-b border-border bg-background/75 pt-[env(safe-area-inset-top)] backdrop-blur-xl", !header && "lg:hidden")}>
          {header ? (
            <div className="mx-auto flex h-16 w-full max-w-7xl items-center gap-2 px-3 sm:px-6 lg:px-10">
              {menu}
              <Link to="/" className="shrink-0 lg:hidden" aria-label={name}>{logo}</Link>
              {header}
            </div>
          ) : (
            <div className="flex h-14 items-center gap-2 px-3">
              {menu}
              <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">{logo} {name}</Link>
            </div>
          )}
        </header>

        <main className={cn("px-4 pt-6 pb-[max(4rem,env(safe-area-inset-bottom))] sm:px-6 lg:px-10", header ? "lg:pt-8" : "lg:pt-10")}>
          <div className={cn("mx-auto w-full", typeof width === "function" ? width(pathname) : width)}>
            <Suspense fallback={loading}>
              <Outlet context={context} />
            </Suspense>
          </div>
        </main>
      </div>
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
      <ScrollRestoration />
    </>
  );
}
