import { useMutation } from "@tanstack/react-query";
import { Check, Copy, Download, Library, Link2, Loader2, Search, TriangleAlert } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Lyrics } from "@/components/find/Lyrics";
import { PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorText, type FindAnswer } from "@/lib/api";
import { useSources } from "@/lib/queries";
import { cn } from "@/lib/utils";

const EXAMPLES = ["Daft Punk - Get Lucky", "https://www.youtube.com/watch?v=…", "https://open.spotify.com/track/…"];

function save(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name.replace(/[\\/:*?"<>|]+/g, " ").trim() });
  a.click();
  URL.revokeObjectURL(url);
}

function Answer({ answer, name }: { answer: FindAnswer; name: string }) {
  const [copied, setCopied] = useState(false);
  const body = answer.synced ? answer.lrc ?? "" : answer.text ?? "";
  const copy = async () => {
    await navigator.clipboard.writeText(body);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Card className="flex min-w-0 flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 font-medium">
            {answer.from === "library" && <Library className="size-4 text-primary" />} {answer.label}
          </p>
          <p className="text-xs text-muted-foreground">
            {answer.error ? answer.error
              : answer.instrumental ? "An instrumental: no words."
              : !answer.found ? "Nothing for this song."
              : answer.synced ? "Synced: every line in time" : "Words only, no timings"}
          </p>
        </div>
        {answer.found && body && (
          <div className="flex gap-1">
            <Button variant="ghost" size="icon-sm" onClick={() => void copy()} aria-label="Copy" title="Copy">{copied ? <Check /> : <Copy />}</Button>
            <Button variant="ghost" size="icon-sm" aria-label="Download" title={`Download .${answer.synced ? "lrc" : "txt"}`}
                    onClick={() => save(`${name}.${answer.synced ? "lrc" : "txt"}`, body)}><Download /></Button>
            {answer.song_id && <Button variant="secondary" size="sm" asChild><Link to={`/songs/${answer.song_id}`}>Open</Link></Button>}
          </div>
        )}
      </div>
      {answer.found && body && <Lyrics text={body} synced={answer.synced} />}
    </Card>
  );
}

/**
 * Find lyrics: a song's name or a link to it (YouTube, Spotify, Deezer, Apple
 * Music, or any page that names the song). The library first, then every
 * source that is on, each answer shown, to copy or download.
 */
export function FindPage() {
  const [params, setParams] = useSearchParams();
  const [typed, setTyped] = useState(params.get("q") ?? "");
  const { data: sources } = useSources();
  const find = useMutation({ mutationFn: api.find });
  const on = sources?.items.filter((s) => s.enabled) ?? [];

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = typed.trim();
    if (!text) return;
    setParams({ q: text }, { replace: true });
    find.mutate(text);
  };

  // Opened with ?q= (a link, or back from a song): asked at once.
  const asked = useRef(false);
  useEffect(() => {
    if (!asked.current && typed.trim()) find.mutate(typed.trim());
    asked.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const result = find.data;
  const name = result ? [result.artist, result.title].filter(Boolean).join(" - ") : "lyrics";
  const found = result?.answers.filter((a) => a.found) ?? [];
  return (
    <>
      <PageHeader title="Find lyrics" description="A song's name, or a link to it: your library first, then every source that is on." />
      <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          {/^https?:\/\//.test(typed) ? <Link2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            : <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />}
          <Input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} className="h-11 pl-9 text-base"
                 placeholder="Artist - Title, or a YouTube / Spotify / Deezer / Apple Music link" aria-label="Song or link" maxLength={500} />
        </div>
        <Button type="submit" className="h-11" disabled={find.isPending || !typed.trim()}>
          {find.isPending ? <Loader2 className="animate-spin" /> : <Search />} Find
        </Button>
      </form>
      <p className="mt-2 text-xs text-muted-foreground">
        For example: {EXAMPLES.map((x, n) => <span key={x}>{n > 0 && ", "}<code className="text-foreground/80">{x}</code></span>)}.
        {" "}Asked of: {on.length ? on.map((s) => s.name).join(", ") : <Link to="/settings/sources" className="text-primary hover:underline">no source yet</Link>}.
      </p>

      {find.error && (
        <p className="mt-6 flex items-center gap-2 text-sm text-destructive" role="alert"><TriangleAlert className="size-4" />{errorText(find.error)}</p>
      )}
      {find.isPending && <p className="mt-6 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Asking every source: a second or two each.</p>}
      {result && !find.isPending && (
        <div className="mt-6">
          <p className="mb-3 text-sm">
            <span className="text-muted-foreground">{result.resolved ? `The link is (via ${result.resolved.via})` : "Looked for"}</span>{" "}
            <span className="font-medium">{result.title}</span>
            {result.artist && <span className="text-muted-foreground"> by </span>}{result.artist && <span className="font-medium">{result.artist}</span>}
            <span className={cn("ml-2 text-xs", found.length ? "text-success" : "text-muted-foreground")}>
              {found.length ? `${found.length} found` : "nothing found"}
            </span>
          </p>
          {!result.artist && <p className="mb-3 text-xs text-muted-foreground">Without an artist the match is loose: "Artist - Title" finds the right song more often.</p>}
          <div className="grid gap-3 lg:grid-cols-2">
            {[...result.answers].sort((a, b) => Number(b.found) - Number(a.found) || Number(b.synced) - Number(a.synced))
              .map((answer, n) => <Answer key={n} answer={answer} name={name} />)}
          </div>
        </div>
      )}
    </>
  );
}
