import { Menu } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { Link, Outlet, ScrollRestoration, useLocation } from "react-router";
import { Toaster } from "sonner";

import { Logo } from "@/components/Logo";
import { useLiveJobs } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { Sidebar } from "./Sidebar";
import { Loading } from "./States";

/**
 * The frame of every screen: the sidebar (a drawer on a phone, opened from the
 * bar) and the screen. The live stream of downloads is opened here, once.
 */
export function Layout() {
  const live = useLiveJobs();
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();

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
        <Sidebar live={live} onClose={() => setOpen(false)} />
      </aside>

      <div className="lg:ml-64">
        <header className="sticky top-0 z-30 border-b border-border bg-background/75 pt-[env(safe-area-inset-top)] backdrop-blur-xl lg:hidden">
          <div className="flex h-14 items-center gap-2 px-3">
            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-label="Open the menu"
              className="flex size-10 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <Menu className="size-5" />
            </button>
            <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <Logo className="size-7" /> videodrop
            </Link>
          </div>
        </header>

        <main className="px-4 pt-6 pb-[max(4rem,env(safe-area-inset-bottom))] sm:px-6 lg:px-10 lg:pt-10">
          <div className="mx-auto w-full max-w-5xl">
            <Suspense fallback={<Loading />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
      <ScrollRestoration />
    </>
  );
}
