import { AudioWaveform, Download, FileText, type LucideIcon, Mic2, MicVocal, Music, PenLine, SlidersVertical, Users } from "lucide-react";
import { Link } from "react-router";
import { useEffect, useMemo, useRef } from "react";

import { api, type VideoDetail } from "@/lib/api";
import { playback, usePlayback } from "@/lib/playback";
import { useCredits, useLyrics } from "@/lib/queries";
import { cn } from "@/lib/utils";

function Panel({ title, icon, actions, children }: { title: string; icon: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col rounded-xl border border-border bg-card/60">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <h2 className="flex items-center gap-2 text-sm font-semibold [&_svg]:size-4 [&_svg]:text-rose-300">{icon}{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function FileLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} download className="flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground">
      <Download className="size-3.5" /> {label}
    </a>
  );
}

/**
 * The song's words, in a box that scrolls on its own. Timed lines follow the
 * song while it plays here ("Song" above), and a click on a line plays from
 * it; otherwise they read as plain text.
 */
export function LyricsPanel({ video }: { video: VideoDetail }) {
  const { data, isPending, error } = useLyrics(video.id, !!(video.youtube_id || video.audio));
  const now = usePlayback();
  const box = useRef<HTMLDivElement>(null);
  const following = now.mode === "audio" && now.videoId === video.audio?.youtube_id;
  const lines = data?.synced ?? [];

  const current = useMemo(() => {
    if (!following || !lines.length) return -1;
    let index = -1;
    for (let i = 0; i < lines.length && lines[i].start <= now.time + 0.25; i++) index = i;
    return index;
  }, [following, lines, now.time]);

  // The line being sung stays in the middle of the box; the page itself doesn't move.
  useEffect(() => {
    const el = box.current?.querySelector<HTMLElement>(`[data-line="${current}"]`);
    if (!el || !box.current) return;
    box.current.scrollTo({ top: el.offsetTop - box.current.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }, [current]);

  const actions = data && (lines.length || data.plain) ? (
    <div className="flex gap-0.5">
      {lines.length > 0 && <FileLink href={api.lyricsFile(video.id, "lrc")} label="LRC" />}
      {data.plain && <FileLink href={api.lyricsFile(video.id, "txt")} label="TXT" />}
    </div>
  ) : null;

  return (
    <Panel title="Lyrics" icon={<Mic2 />} actions={actions}>
      <div ref={box} className="rail relative h-72 overflow-y-auto px-4 py-3">
        {isPending ? (
          <div className="flex flex-col gap-2" aria-hidden>
            {Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton h-3.5 rounded" style={{ width: `${55 + ((i * 17) % 40)}%` }} />)}
          </div>
        ) : error ? (
          <p className="text-sm text-muted-foreground">The lyrics couldn't be loaded.</p>
        ) : lines.length ? (
          <ol className="flex flex-col gap-1.5">
            {lines.map((line, i) => (
              <li key={i} data-line={i}>
                <button type="button" onClick={() => following && playback.seek(line.start)} disabled={!following}
                        className={cn("text-left text-[15px] leading-snug transition-colors",
                                      following ? "cursor-pointer hover:text-foreground" : "cursor-default",
                                      i === current ? "font-semibold text-foreground" : following ? "text-muted-foreground/70" : "text-foreground/85",
                                      !line.text && "h-3")}>
                  {line.text || ""}
                </button>
              </li>
            ))}
          </ol>
        ) : data?.plain ? (
          <p className="text-[15px] leading-relaxed whitespace-pre-line text-foreground/85">{data.plain}</p>
        ) : (
          <p className="text-sm text-muted-foreground">{data?.instrumental ? "An instrumental: no lyrics." : "Lyrics not available."}</p>
        )}
      </div>
      {(data?.source || lines.length > 0) && (
        <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
          {lines.length > 0 && !following && video.audio ? "Timed to the song: play “Song” above to follow along. " : ""}
          {lines.length > 0 && following ? "Click a line to play from it. " : ""}
          {data?.source && <>From {data.source}.</>}
        </p>
      )}
    </Panel>
  );
}

const ROLES: [RegExp, LucideIcon][] = [
  [/perform|vocal|sing|feat/i, MicVocal],
  [/writ|lyric/i, PenLine],
  [/compos/i, Music],
  [/produc/i, SlidersVertical],
  [/mix|master|engineer/i, AudioWaveform],
];

function RoleIcon({ role }: { role: string }) {
  const Icon = ROLES.find(([pattern]) => pattern.test(role))?.[1] ?? Users;
  return <Icon className="size-3.5" aria-hidden />;
}

/** Who made the song, as YouTube Music credits it. */
export function CreditsPanel({ video }: { video: VideoDetail }) {
  const { data, isPending } = useCredits(video.id, !!(video.youtube_id || video.audio));
  return (
    <Panel title="Credits" icon={<FileText />}>
      <div className="flex flex-col gap-3 px-4 py-3">
        {isPending ? (
          Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="flex flex-col gap-1.5" aria-hidden>
              <div className="skeleton h-3 w-24 rounded" />
              <div className="skeleton h-3.5 w-48 rounded" />
            </div>
          ))
        ) : !data?.credits.length ? (
          <p className="text-sm text-muted-foreground">No credits for this song.</p>
        ) : (
          <>
            <dl className="flex flex-col gap-3">
              {data.credits.map((credit) => (
                <div key={credit.role}>
                  <dt className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    <RoleIcon role={credit.role} /> {credit.role}
                  </dt>
                  <dd className="mt-1 flex flex-wrap gap-1.5">
                    {(credit.people?.length ? credit.people : credit.names.map((name) => ({ name, id: null }))).map((person) => (
                      // Their page when YouTube Music has one; else what a search finds of their work.
                      <Link key={person.name} to={person.id ? `/artist/${person.id}` : `/search?q=${encodeURIComponent(person.name)}`}
                            title={person.id ? `${person.name}: their page` : `Search for ${person.name}`}
                            className="rounded-full border border-border bg-secondary/60 px-2.5 py-0.5 text-sm transition hover:border-rose-400/40 hover:bg-accent">
                        {person.name}
                      </Link>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
            {data.provider && <p className="text-[11px] text-muted-foreground">Credits from {data.provider}, through YouTube Music.</p>}
          </>
        )}
      </div>
    </Panel>
  );
}
