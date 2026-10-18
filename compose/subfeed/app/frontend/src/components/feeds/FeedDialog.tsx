import { Loader2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api, errorText, type Feed } from "@/lib/api";
import { useFeedsChanged, useSettings, useSources } from "@/lib/queries";

/** Makes a feed, or changes one (saved). Only the community is needed; the rest has defaults. */
export function FeedDialog({ saved, open, onOpenChange, onSaved }: {
  saved?: Feed;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (feed: Feed) => void;
}) {
  const changed = useFeedsChanged();
  const { data: sources } = useSources();
  const { data: settings } = useSettings();
  const source = sources?.sources.find((s) => s.key === (saved?.source ?? "reddit")) ?? sources?.sources[0];
  const [target, setTarget] = useState("");
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTarget(saved?.target ?? "");
    setTitle(saved?.title ?? "");
    setMinutes(saved ? String(saved.poll_minutes) : "");
    setEnabled(saved?.enabled ?? true);
  }, [open, saved]);

  const moved = !!saved && target.trim().toLowerCase() !== saved.target.toLowerCase();

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    const poll_minutes = minutes ? Number(minutes) : undefined;
    try {
      const feed = saved
        ? await api.updateFeed(saved.slug, { target: moved ? target : undefined, title: title.trim() || undefined, poll_minutes, enabled })
        : await api.createFeed({ target, source: source?.key ?? "reddit", title: title.trim() || undefined, poll_minutes, enabled });
      toast.success(saved ? "Saved" : `${feed.title} is being read`);
      changed();
      onSaved?.(feed);
      onOpenChange(false);
    } catch (error) {
      toast.error(errorText(error, "Couldn't save it."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <form onSubmit={save}>
          <DialogHeader>
            <DialogTitle>{saved ? `Change ${saved.title}` : "New feed"}</DialogTitle>
            <DialogDescription>
              {saved ? "Another community starts the feed afresh: the posts found so far go."
                : "subfeed reads the community now: first its history, then what is new, every so often."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-5">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="feed-target">{source?.target_label ?? "Subreddit"}</Label>
              <Input id="feed-target" value={target} onChange={(e) => setTarget(e.target.value)} required autoFocus={!saved}
                     placeholder="SexyMusicVideos" autoComplete="off" spellCheck={false} maxLength={200} />
              <p className="text-xs text-muted-foreground">
                {source?.target_help ?? ""} Its name, r/Name, or its address.
              </p>
              {moved && <p className="text-xs text-warning">The {saved.posts} posts found so far will go.</p>}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="feed-title">Name <span className="font-normal text-muted-foreground">(optional)</span></Label>
              <Input id="feed-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80}
                     placeholder={target ? `r/${target.replace(/^.*\/?r\//, "").replace(/\/$/, "")}` : "r/Name"} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="feed-minutes">Read every (minutes)</Label>
              <Input id="feed-minutes" type="number" min={15} max={1440} value={minutes} onChange={(e) => setMinutes(e.target.value)}
                     placeholder={String(settings?.default_poll_minutes ?? 30)} />
              <p className="text-xs text-muted-foreground">15 to 1440. Reddit limits how often one address asks: all feeds share it.</p>
            </div>
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="feed-enabled">Read it</Label>
              <Switch id="feed-enabled" checked={enabled} onCheckedChange={setEnabled} />
            </div>
          </DialogBody>
          <DialogFooter className="justify-end">
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !target.trim()}>
              {busy && <Loader2 className="animate-spin" />} {saved ? "Save" : "Make it"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
