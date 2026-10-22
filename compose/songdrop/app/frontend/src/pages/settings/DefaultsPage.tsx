import { OptionsPanel } from "@/components/jobs/OptionsPanel";
import { SettingGroup } from "@/components/settings/rows";
import { SettingsScreen } from "@/components/settings/SettingsScreen";
import { useSettingsForm } from "@/components/settings/useSettingsForm";
import type { Engine } from "@/lib/api";
import { useOptionsSpec } from "@/lib/hooks";

const KEYS = ["defaults"] as const;

const TEXT: Record<Engine, { title: string; description: string }> = {
  spotdl: {
    title: "Spotify",
    description: "Where the options of a Spotify download start. spotDL finds each song on YouTube Music; a download can still change them.",
  },
  ytdlp: {
    title: "Other sites",
    description: "Where the options of a YouTube, SoundCloud, Bandcamp… download start. yt-dlp downloads them; a download can still change them.",
  },
};

/** The default download options of one engine. */
export function DefaultsPage({ engine }: { engine: Engine }) {
  const form = useSettingsForm(KEYS);
  const spec = useOptionsSpec();
  const { draft, set } = form;
  // A clip is for one video, never a default.
  const fields = spec?.engines[engine].fields.filter((field) => field.type !== "time") ?? [];
  const builtIn = Object.fromEntries(fields.map((field) => [field.key, field.builtin]));

  return (
    <SettingsScreen title={TEXT[engine].title} description={TEXT[engine].description} form={form} ready={!!draft && !!spec}>
      {draft && spec && (
        <SettingGroup>
          <div className="px-4 pb-4">
            <OptionsPanel
              title={spec.engines[engine].label}
              fields={fields}
              values={{ ...builtIn, ...draft.defaults[engine] }}
              onChange={(next) => set("defaults", { ...draft.defaults, [engine]: next })}
              onReset={() => set("defaults", { ...draft.defaults, [engine]: builtIn })}
              plain
            />
          </div>
        </SettingGroup>
      )}
    </SettingsScreen>
  );
}
