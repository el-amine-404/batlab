import { Menu } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { Link, Outlet, ScrollRestoration, useLocation } from "react-router";
import { Toaster } from "sonner";

import { Logo } from "@/components/Logo";
import { cn } from "@/lib/utils";

import { SearchBox } from "./SearchBox";
import { Sidebar } from "./Sidebar";
import { Loading } from "./States";

/** The frame of every screen: the sidebar (a drawer on a phone), the search, and the screen. */
export function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const settings = pathname.startsWith("/settings");

  useEffect(() => setOpen(false), [pathname]);

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
        <Sidebar onClose={() => setOpen(false)} />
      </aside>

      <div className="lg:ml-64">
        <header className="sticky top-0 z-30 border-b border-border bg-background/75 pt-[env(safe-area-inset-top)] backdrop-blur-xl">
          <div className="mx-auto flex h-16 w-full max-w-7xl items-center gap-2 px-3 sm:px-6 lg:px-10">
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="Open the menu"
              className="flex size-10 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
            >
              <Menu className="size-5" />
            </button>
            <Link to="/" className="shrink-0 lg:hidden" aria-label="clipseerr">
              <Logo className="size-8" />
            </Link>
            <SearchBox className="max-w-xl" />
          </div>
        </header>

        <main className="px-4 pt-6 pb-[max(4rem,env(safe-area-inset-bottom))] sm:px-6 lg:px-10 lg:pt-8">
          <div className={cn("mx-auto w-full", settings ? "max-w-4xl" : "max-w-7xl")}>
            <Suspense fallback={<Loading />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
      {/* Back finds a long list where it was left (its pages are cached); a new page opens at the top. */}
      <ScrollRestoration />
    </>
  );
}
