import { CloudOff, Inbox } from "lucide-react";
import { Toaster } from "sonner";

import { CookiesCard } from "@/components/CookiesCard";
import { JobCard } from "@/components/JobCard";
import { LinkForm } from "@/components/LinkForm";
import { Logo } from "@/components/Logo";
import { Card } from "@/components/ui/card";
import { useJobs } from "@/lib/hooks";

export function App() {
  const { jobs, error, refresh } = useJobs();

  return (
    <>
      <div className="backdrop" aria-hidden />
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 pt-[max(2rem,env(safe-area-inset-top))] pb-[max(3rem,env(safe-area-inset-bottom))] sm:pt-16">
        <header className="flex items-center gap-3.5">
          <Logo className="size-12" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">songdrop</h1>
            <p className="text-sm text-muted-foreground">Paste a link. Get the song in your library.</p>
          </div>
        </header>

        <LinkForm onCreated={refresh} />

        <section aria-labelledby="downloads" className="flex flex-col gap-3">
          <div className="flex items-center justify-between px-1">
            <h2 id="downloads" className="text-sm font-medium text-muted-foreground">
              Downloads
            </h2>
            {jobs && jobs.length > 0 && <span className="text-xs text-muted-foreground tabular-nums">{jobs.length}</span>}
          </div>

          {error && (
            <Card className="flex items-center gap-3 border-destructive/30 p-4 text-sm text-destructive">
              <CloudOff className="size-4 shrink-0" /> {error}
            </Card>
          )}

          {jobs && jobs.length === 0 && (
            <Card className="flex flex-col items-center gap-2 px-6 py-10 text-center">
              <Inbox className="size-8 text-muted-foreground/60" />
              <p className="text-sm font-medium">Nothing here yet</p>
              <p className="max-w-xs text-xs text-muted-foreground">
                Paste a song, album or playlist link above. Each song is checked and added to Navidrome.
              </p>
            </Card>
          )}

          {jobs?.map((job) => <JobCard key={job.id} job={job} onChange={refresh} />)}
        </section>

        <CookiesCard />

        <footer className="px-1 text-center text-xs leading-relaxed text-muted-foreground/70">
          Spotify links go through spotDL, everything else through yt-dlp. Songs are checked by musicguard, then
          appear in Navidrome; download links last a day.
        </footer>
      </main>
      <Toaster theme="dark" position="top-center" richColors closeButton />
    </>
  );
}
