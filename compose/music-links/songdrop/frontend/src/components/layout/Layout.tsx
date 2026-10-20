import { Menu } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { Toaster } from "sonner";

import { Logo } from "@/components/Logo";
import { JobsProvider } from "@/lib/jobs";
import { cn } from "@/lib/utils";

import { Sidebar } from "./Sidebar";
import { Loading } from "./States";

/** The frame of every screen: the sidebar (a drawer on a phone) and the screen. */
export function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => {
    setOpen(false);
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <JobsProvider>
      <div className="backdrop" aria-hidden />

      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-background/80 px-3 pt-[env(safe-area-inset-top)] backdrop-blur-xl lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open the menu"
          className="flex size-10 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Menu className="size-5" />
        </button>
        <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <Logo className="size-7" /> songdrop
        </Link>
      </header>

      {open && <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-64 -translate-x-full border-r border-border bg-background/95 backdrop-blur-xl transition-transform lg:translate-x-0 lg:bg-background/60",
          open && "translate-x-0",
        )}
      >
        <Sidebar onClose={() => setOpen(false)} />
      </aside>

      <main className="px-4 pt-6 pb-[max(4rem,env(safe-area-inset-bottom))] sm:px-6 lg:ml-64 lg:px-10 lg:pt-10">
        <div className="mx-auto w-full max-w-4xl">
          {/* Settings and System load their code when opened (app/navigation.tsx). */}
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </div>
      </main>
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
    </JobsProvider>
  );
}
