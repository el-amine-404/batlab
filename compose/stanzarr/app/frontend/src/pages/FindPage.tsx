import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, Disc3, Download, Library, Link2, Loader2, Search, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Lyrics } from "@/components/find/Lyrics";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorText, type FindAnswer, type FindResult, type FindSong } from "@/lib/api";
import { length } from "@/lib/format";
import { cn } from "@/lib/utils";

const isLink = (text: string) => /^[a-z][a-z0-9+.-]*:\/\//i.test(text.trim());
const plain = (text: string) => text.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "");

function save(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name.replace(/[\\/:*?"<>|]+/g, " ").trim() });
  a.click();
  URL.revokeObjectURL(url);
}

function Cover({ src, className }: { src?: string | null; className?: string }) {
  const [gone, setGone] = useState(false);
  if (!src || gone) {
    return <span className={cn("flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground", className)}><Disc3 className="size-1/2" /></span>;
  }
  return <img src={src} alt="" loading="lazy" onError={() => setGone(true)} className={cn("shrink-0 rounded-md object-cover", className)} />;
}

/** Which song the name means: one line each, the closest first. */
function Choices({ songs, onPick }: { songs: FindSong[]; onPick: (song: FindSong) => void }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-sm font-medium text-muted-foreground">Which song?</h2>
      <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
        {songs.map((song, n) => (
          <li key={n}>
            <button type="button" onClick={() => onPick(song)}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none sm:px-4">
              <Cover src={song.cover} className="size-11" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{song.title}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {song.artist}{song.album ? ` · ${song.album}` : ""}{song.duration ? ` · ${length(song.duration)}` : ""}
                </span>
              </span>
              {song.song_id ? <span className="shrink-0 rounded-full bg-primary/15 px-2 py-0.5 text-[11px] text-primary">In your library</span>
                : song.typed ? <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">As typed</span> : null}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Answer({ answer, name, result }: { answer: FindAnswer; name: string; result: FindResult }) {
  const [copied, setCopied] = useState(false);
  const body = answer.synced ? answer.lrc ?? "" : answer.text ?? "";
  const matched = answer.matched;
  const other = matched?.title && (plain(matched.title) !== plain(result.title) || (matched.artist && !plain(matched.artist).includes(plain(result.artist).slice(0, 6))));
  const copy = async () => {
    await navigator.clipboard.writeText(body);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {answer.synced ? "Synced: every line in time." : "Words only, no timings."}
          {other && <> Matched <span className="text-foreground">{matched.artist} – {matched.title}</span>{matched.duration ? ` (${length(matched.duration)})` : ""}: check it's the right one.</>}
        </p>
        <div className="flex gap-1">
          <Button variant="secondary" size="sm" onClick={() => void copy()}>{copied ? <Check /> : <Copy />} Copy</Button>
          <Button variant="secondary" size="sm" onClick={() => save(`${name}.${answer.synced ? "lrc" : "txt"}`, body)}>
            <Download /> .{answer.synced ? "lrc" : "txt"}
          </Button>
          {answer.song_id && <Button size="sm" asChild><Link to={`/songs/${answer.song_id}`}>Open</Link></Button>}
        </div>
      </div>
      <Lyrics text={body} synced={answer.synced} />
    </div>
  );
}

/** The chosen song's lyrics: a tab for each source that had them, the others said in one line. */
function Result({ result, onBack }: { result: FindResult; onBack?: () => void }) {
  const found = [...result.answers.filter((a) => a.found)].sort((a, b) => Number(b.from === "library") - Number(a.from === "library") || Number(b.synced) - Number(a.synced));
  const [tab, setTab] = useState(0);
  const missing = result.answers.filter((a) => !a.found);
  const name = [result.artist, result.title].filter(Boolean).join(" - ");
  const shown = found[Math.min(tab, found.length - 1)];
  return (
    <section className="mt-6 flex flex-col gap-4">
      {onBack && (
        <button type="button" onClick={onBack} className="flex items-center gap-1.5 self-start text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Other songs
        </button>
      )}
      <div className="flex items-center gap-3">
        <Cover src={result.resolved?.cover} className="size-14" />
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{result.title}</h2>
          <p className="truncate text-sm text-muted-foreground">
            {result.artist}{result.album ? ` · ${result.album}` : ""}{result.duration ? ` · ${length(result.duration)}` : ""}
            {result.resolved && ` · from the ${result.resolved.via} link`}
          </p>
          {result.asked_as && <p className="text-xs text-muted-foreground">Found as “{result.asked_as}”: nothing has the full title.</p>}
        </div>
      </div>

      {!found.length ? (
        <Card className="p-5 text-sm">
          <p className="font-medium">No lyrics for this song.</p>
          <p className="mt-1 text-muted-foreground">
            None of the sources has it{missing.some((a) => a.error) ? " (some couldn't be asked, below)" : ""}. More sources (Musixmatch, Genius) can be added in{" "}
            <Link to="/settings/sources" className="text-primary hover:underline">Settings, Sources</Link>.
          </p>
        </Card>
      ) : (
        <Card className="p-0">
          <div className="flex gap-1 overflow-x-auto border-b border-border p-2" role="tablist">
            {found.map((answer, n) => (
              <button key={n} type="button" role="tab" aria-selected={n === tab} onClick={() => setTab(n)}
                      className={cn("flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm hover:bg-accent", n === tab && "bg-accent font-medium")}>
                {answer.from === "library" && <Library className="size-3.5 text-primary" />}
                {answer.label}
                <span className={cn("rounded px-1 text-[10px] uppercase", answer.synced ? "bg-primary/15 text-primary" : "bg-secondary text-muted-foreground")}>
                  {answer.synced ? "synced" : "words"}
                </span>
              </button>
            ))}
          </div>
          <div className="p-3 sm:p-4"><Answer key={tab} answer={shown} name={name} result={result} /></div>
        </Card>
      )}
      {missing.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Not found at {missing.map((a) => a.error ? `${a.label} (${a.error})` : a.label).join(", ")}.
        </p>
      )}
    </section>
  );
}

/**
 * Find lyrics: type a song (then pick which one), or paste a link to it.
 * The library first, then every source that is on.
 */
export function FindPage() {
  const [params, setParams] = useSearchParams();
  const [typed, setTyped] = useState(params.get("q") ?? "");
  const songs = useMutation({ mutationFn: api.findSongs });
  const find = useMutation({ mutationFn: api.find });
  const [picked, setPicked] = useState(false);

  const open = (song: FindSong) => {
    setPicked(true);
    find.mutate({ artist: song.artist, title: song.title, album: song.album, duration: song.duration });
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = typed.trim();
    if (!text) return;
    setParams({ q: text }, { replace: true });
    find.reset();
    songs.reset();
    if (isLink(text)) {
      setPicked(true);
      find.mutate({ query: text });
      return;
    }
    setPicked(false);
    songs.mutate(text, {
      onSuccess: ({ songs: list }) => {
        // "Artist - Title" typed exactly as a song is: no need to ask which.
        const [artist, title] = text.split(/\s[-–—|]\s/);
        const first = list[0];
        if (first && title && plain(first.title) === plain(title) && plain(first.artist) === plain(artist)) open(first);
      },
    });
  };

  const busy = songs.isPending || find.isPending;
  const error = songs.error ?? find.error;
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Find lyrics</h1>
      <form onSubmit={submit} className="mt-4 flex gap-2">
        <div className="relative min-w-0 flex-1">
          {isLink(typed) ? <Link2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            : <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />}
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="h-11 pl-9 text-base" enterKeyHint="search"
                 placeholder="A song, or a link to it" aria-label="A song, or a link to it" maxLength={500} />
        </div>
        <Button type="submit" className="h-11 px-4" disabled={busy || !typed.trim()} aria-label="Find">
          {busy ? <Loader2 className="animate-spin" /> : <Search />}<span className="hidden sm:inline">Find</span>
        </Button>
      </form>
      {!songs.data && !find.data && !busy && !error && (
        <p className="mt-2 text-xs text-muted-foreground">Artist - Title finds it fastest. Links from YouTube, Spotify, Deezer and Apple Music work too.</p>
      )}

      {error && <p className="mt-6 flex items-center gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="size-4" />{errorText(error)}</p>}
      {songs.isPending && <p className="mt-6 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Looking for the song…</p>}
      {find.isPending && <p className="mt-6 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Asking the sources: a second or two each…</p>}

      {songs.data && !picked && (songs.data.songs.length
        ? <Choices songs={songs.data.songs} onPick={open} />
        : <p className="mt-6 text-sm text-muted-foreground">No song by that name. Try "Artist - Title".</p>)}
      {find.data && picked && !find.isPending && (
        <Result key={`${find.data.artist}-${find.data.title}`} result={find.data}
                onBack={songs.data?.songs.length ? () => { setPicked(false); find.reset(); } : undefined} />
      )}
    </div>
  );
}
