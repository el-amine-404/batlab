import { BookOpen, Download, Settings as SettingsIcon } from "lucide-react";
import { Toaster } from "sonner";

import { Logo } from "@/components/Logo";
import { hrefFor, type Route, useRoute } from "@/lib/route";
import { cn } from "@/lib/utils";
import { DownloadsPage } from "@/pages/DownloadsPage";
import { SettingsPage } from "@/pages/SettingsPage";

const TABS: { route: Route; label: string; icon: typeof Download }[] = [
  { route: "downloads", label: "Downloads", icon: Download },
  { route: "settings", label: "Settings", icon: SettingsIcon },
];

export function App() {
  const route = useRoute();

  return (
    <>
      <div className="backdrop" aria-hidden />
      <header className="sticky top-0 z-20 border-b border-border bg-background/75 pt-[env(safe-area-inset-top)] backdrop-blur-xl">
        <div className="mx-auto flex h-14 w-full max-w-3xl items-center gap-2 px-4">
          <a href={hrefFor("downloads")} className="mr-2 flex items-center gap-2.5 font-semibold tracking-tight">
            <Logo className="size-7" />
            <span className="hidden sm:inline">songdrop</span>
          </a>
          <nav className="flex items-center gap-1" aria-label="Main">
            {TABS.map(({ route: target, label, icon: Icon }) => (
              <a
                key={target}
                href={hrefFor(target)}
                aria-current={route === target ? "page" : undefined}
                className={cn(
                  "flex h-9 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                  route === target && "bg-accent text-foreground",
                )}
              >
                <Icon className="size-4" /> {label}
              </a>
            ))}
          </nav>
          <a
            href="/docs/"
            target="_blank"
            rel="noopener"
            className="ml-auto flex h-9 items-center gap-2 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <BookOpen className="size-4" /> <span className="hidden sm:inline">Docs</span>
          </a>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl px-4 pt-6 pb-[max(3rem,env(safe-area-inset-bottom))] sm:pt-8">
        {route === "settings" ? <SettingsPage /> : <DownloadsPage />}
      </main>
      <Toaster theme="dark" position="top-center" richColors closeButton />
    </>
  );
}
