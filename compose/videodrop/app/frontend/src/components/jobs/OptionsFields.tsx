import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { OptionsPatch, VideoOptions } from "@/lib/api";

import { CODEC, CONTAINER, QUALITY } from "./labels";

const INHERIT = "__inherit";

function Choice<K extends "quality" | "codec" | "container">({ name, label, labels, value, inherited, onChange, id, inheritLabel }: {
  name: K;
  inheritLabel: string;
  label: string;
  labels: Record<VideoOptions[K], string>;
  value: OptionsPatch;
  inherited: VideoOptions;
  onChange: (value: OptionsPatch) => void;
  id: string;
}) {
  const current = value[name] ?? INHERIT;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${id}-${name}`}>{label}</Label>
      <Select value={current} onValueChange={(next) => onChange({ ...value, [name]: next === INHERIT ? undefined : next })}>
        <SelectTrigger id={`${id}-${name}`}><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={INHERIT}>{inheritLabel} ({labels[inherited[name]]})</SelectItem>
          {Object.entries(labels).map(([key, text]) => <SelectItem key={key} value={key}>{text as string}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

function Toggle({ name, label, value, inherited, onChange, id }: {
  inheritLabel: string;
  name: "embed_metadata" | "embed_thumbnail" | "embed_chapters" | "auto_subtitles" | "playlist";
  label: string;
  value: OptionsPatch;
  inherited: VideoOptions;
  onChange: (value: OptionsPatch) => void;
  id: string;
}) {
  const on = value[name] ?? inherited[name];
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
      <Label htmlFor={`${id}-${name}`} className="font-normal">{label}</Label>
      <Switch id={`${id}-${name}`} checked={on} onCheckedChange={(next) => onChange({ ...value, [name]: next === inherited[name] ? undefined : next })} />
    </div>
  );
}

/**
 * Download options as a form. What is left on "Default" isn't sent: the next
 * layer decides (the destination's, then the settings'); `inherited` is what
 * that gives, shown beside it.
 */
export function OptionsFields({ value, inherited, onChange, id, inheritLabel = "Default" }: {
  value: OptionsPatch;
  inherited: VideoOptions;
  onChange: (value: OptionsPatch) => void;
  id: string;
  /** What "not set" is called: "Default", or "As now" where the settings themselves are changed. */
  inheritLabel?: string;
}) {
  const props = { value, inherited, onChange, id, inheritLabel };
  // Typed as text ("en, fr"); a list once parsed.
  const [subtitles, setSubtitles] = useState(value.subtitles?.join(", ") ?? "");
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Choice name="quality" label="Quality" labels={QUALITY} {...props} />
        <Choice name="codec" label="Video codec" labels={CODEC} {...props} />
        <Choice name="container" label="File" labels={CONTAINER} {...props} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-subtitles`}>Subtitles</Label>
        <Input id={`${id}-subtitles`} placeholder={inherited.subtitles.join(", ") || "none"} spellCheck={false}
               value={subtitles}
               onChange={(e) => {
                 setSubtitles(e.target.value);
                 const list = e.target.value.split(/[,\s]+/).filter(Boolean);
                 onChange({ ...value, subtitles: list.length ? list : undefined });
               }} />
        <p className="text-xs text-muted-foreground">Languages to embed, as en, fr, pt-BR; empty: the default.</p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Toggle name="embed_metadata" label="Title, artist and date in the file" {...props} />
        <Toggle name="embed_thumbnail" label="Picture as the cover" {...props} />
        <Toggle name="embed_chapters" label="Chapters" {...props} />
        <Toggle name="auto_subtitles" label="Automatic captions too" {...props} />
        <Toggle name="playlist" label="Whole playlist" {...props} />
        <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
          <Label htmlFor={`${id}-limit`} className="font-normal">Playlist: at most</Label>
          <Input id={`${id}-limit`} type="number" min={1} max={500} className="h-8 w-20" placeholder={String(inherited.playlist_limit)}
                 value={value.playlist_limit ?? ""}
                 onChange={(e) => onChange({ ...value, playlist_limit: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </div>
    </div>
  );
}
