import { Check, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { OptionsPatch, VideoOptions } from "@/lib/api";
import { cn } from "@/lib/utils";

import { CODEC, CONTAINER, QUALITY } from "./labels";

const INHERIT = "__inherit";
// Offered first; any other code can be added.
const COMMON = ["en", "fr", "ar", "es", "de", "pt", "it", "nl", "tr", "ru", "ja", "ko", "zh"];
const languageNames = new Intl.DisplayNames(undefined, { type: "language" });

export function languageName(code: string): string {
  try {
    return languageNames.of(code) ?? code;
  } catch {
    return code;
  }
}

type Props = {
  value: OptionsPatch;
  /** What applies to what isn't set here: the next layer's values. */
  inherited: VideoOptions;
  onChange: (value: OptionsPatch) => void;
  id: string;
  /**
   * fixed: the settings' own defaults, edited directly (no "Default" choice);
   * else what isn't set is "Default (…)", the next layer decides.
   */
  fixed?: boolean;
  /** What the video has (from its preview), to mark the languages that exist. */
  available?: { subtitles: string[]; automatic: number; automaticEnglish: boolean };
};

function Section({ title, help, children }: { title: string; help?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <p className="text-sm font-medium">{title}</p>
        {help && <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{help}</p>}
      </div>
      {children}
    </section>
  );
}

function Choice<K extends "quality" | "codec" | "container">({ name, label, labels, help, props }: {
  name: K;
  label: string;
  labels: Record<VideoOptions[K], string>;
  help: string;
  props: Props;
}) {
  const { value, inherited, onChange, id, fixed } = props;
  const current = value[name] ?? (fixed ? inherited[name] : INHERIT);
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${id}-${name}`}>{label}</Label>
      <Select value={current} onValueChange={(next) => onChange({ ...value, [name]: next === INHERIT || (fixed && next === inherited[name]) ? undefined : next })}>
        <SelectTrigger id={`${id}-${name}`}><SelectValue /></SelectTrigger>
        <SelectContent>
          {!fixed && <SelectItem value={INHERIT}>Default ({labels[inherited[name]]})</SelectItem>}
          {Object.entries(labels).map(([key, text]) => <SelectItem key={key} value={key}>{text as string}</SelectItem>)}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">{help}</p>
    </div>
  );
}

function Toggle({ name, label, help, props }: {
  name: "embed_metadata" | "embed_thumbnail" | "embed_chapters" | "auto_subtitles" | "lyrics" | "playlist";
  label: string;
  help?: string;
  props: Props;
}) {
  const { value, inherited, onChange, id } = props;
  const on = value[name] ?? inherited[name];
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
      <div>
        <Label htmlFor={`${id}-${name}`} className="font-normal">{label}</Label>
        {help && <p className="mt-0.5 text-xs text-muted-foreground">{help}</p>}
      </div>
      <Switch id={`${id}-${name}`} checked={on} className="mt-0.5"
              onCheckedChange={(next) => onChange({ ...value, [name]: next === inherited[name] ? undefined : next })} />
    </div>
  );
}

/** Subtitle languages as buttons: the common ones, the video's own, and any code typed. */
function Languages({ props }: { props: Props }) {
  const { value, inherited, onChange, available } = props;
  const chosen = value.subtitles ?? inherited.subtitles;
  const [other, setOther] = useState("");
  const offered = [...new Set([...(available?.subtitles ?? []).filter((c) => !c.includes("-") || c.length <= 5), ...COMMON, ...chosen])];
  const toggle = (code: string) => {
    const next = chosen.includes(code) ? chosen.filter((c) => c !== code) : [...chosen, code];
    onChange({ ...value, subtitles: JSON.stringify(next) === JSON.stringify(inherited.subtitles) ? undefined : next });
  };
  const has = (code: string) => available?.subtitles.includes(code);
  const addOther = () => {
    const code = other.trim();
    if (/^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$/.test(code) && !chosen.includes(code)) toggle(code);
    setOther("");
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {offered.map((code) => {
          const on = chosen.includes(code);
          return (
            <button key={code} type="button" onClick={() => toggle(code)} aria-pressed={on}
                    title={has(code) ? "This video has real subtitles in this language" : undefined}
                    className={cn("flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors",
                                  on ? "border-sky-400/50 bg-sky-400/15 text-sky-200" : "border-border text-muted-foreground hover:text-foreground")}>
              {on && <Check className="size-3" />}
              {languageName(code)}
              {has(code) && <span className="size-1.5 rounded-full bg-success" aria-label="available" />}
            </button>
          );
        })}
        {/* Inside the download's own form: Enter adds the code, it doesn't send the form. */}
        <div className="flex items-center">
          <Input value={other} onChange={(e) => setOther(e.target.value)} placeholder="other code" aria-label="Another language code"
                 className="h-7 w-24 rounded-full px-2.5 text-xs"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addOther(); } }} />
          <button type="button" onClick={addOther} aria-label="Add the language" className="ml-1 text-muted-foreground hover:text-foreground"><Plus className="size-4" /></button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {chosen.length ? `Embedded: ${chosen.map(languageName).join(", ")}.` : "None chosen: no subtitles."}
        {available && (available.subtitles.length
          ? <> This video has real subtitles in {available.subtitles.length} language{available.subtitles.length > 1 ? "s" : ""} <span className="inline-block size-1.5 rounded-full bg-success align-middle" />.</>
          : " This video has no real subtitles.")}
        {available && available.automatic > 0 && ` Automatic captions exist in ${available.automatic} languages${available.automaticEnglish ? ", English among them" : ""}.`}
      </p>
    </div>
  );
}

/**
 * How a video is downloaded, in sections a person can follow. Used for one
 * download, a destination's options and the settings' defaults.
 */
export function OptionsFields(props: Props) {
  return (
    <div className="flex flex-col gap-6">
      <Section title="Picture and file">
        <div className="grid gap-4 sm:grid-cols-3">
          <Choice name="quality" label="Quality" labels={QUALITY} help="The highest it takes; Best is the highest there is." props={props} />
          <Choice name="codec" label="Video codec" labels={CODEC} help="H.264 plays on every TV without converting (up to 1080p on YouTube)." props={props} />
          <Choice name="container" label="File type" labels={CONTAINER} help="MP4 plays everywhere, Jellyfin included." props={props} />
        </div>
      </Section>

      <Section title="Subtitles and lyrics"
               help="Subtitles are the words said in the video, written at the bottom of the screen. The ones chosen are put inside the file; Jellyfin and most players let you turn them on or off.">
        <Languages props={props} />
        <div className="grid gap-2 sm:grid-cols-2">
          <Toggle name="auto_subtitles" label="Automatic captions when there are no real ones"
                  help="Made by the site's speech recognition: often good, sometimes wrong." props={props} />
          <Toggle name="lyrics" label="Lyrics (music videos)"
                  help="The song's lyrics, timed to the music, from LRCLIB, as a subtitle track named Lyrics." props={props} />
        </div>
      </Section>

      <Section title="Inside the file">
        <div className="grid gap-2 sm:grid-cols-3">
          <Toggle name="embed_metadata" label="Title, artist and date" props={props} />
          <Toggle name="embed_thumbnail" label="Picture as the cover" props={props} />
          <Toggle name="embed_chapters" label="Chapters" props={props} />
        </div>
      </Section>

      <Section title="Playlists" help="When the link is a video inside a playlist.">
        <div className="grid gap-2 sm:grid-cols-2">
          <Toggle name="playlist" label="The whole playlist, not only the video" props={props} />
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
            <Label htmlFor={`${props.id}-limit`} className="font-normal">At most (videos)</Label>
            <Input id={`${props.id}-limit`} type="number" min={1} max={500} className="h-8 w-20" placeholder={String(props.inherited.playlist_limit)}
                   value={props.value.playlist_limit ?? ""}
                   onChange={(e) => props.onChange({ ...props.value, playlist_limit: e.target.value ? Number(e.target.value) : undefined })} />
          </div>
        </div>
      </Section>
    </div>
  );
}
