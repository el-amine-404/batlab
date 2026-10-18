import { OptionsFields } from "@/components/jobs/OptionsFields";
import { Section, SettingsScreen } from "@/components/settings/fields";
import type { OptionsPatch, VideoOptions } from "@/lib/api";

/** What differs between two option sets, as a patch. */
function diff(from: VideoOptions, to: VideoOptions): OptionsPatch {
  return Object.fromEntries(Object.entries(to).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(from[key as keyof VideoOptions])));
}

export default function DefaultOptionsPage() {
  return (
    <SettingsScreen title="Default options" description="What a download takes when neither it nor its destination says otherwise.">
      {(draft) => {
        const saved = draft.saved!.defaults;
        const current = draft.value("defaults") ?? saved;
        return (
          <Section title="Every download" description="A destination can set its own (Destinations); a download, its own again.">
            <OptionsFields id="defaults" fixed value={diff(saved, current)} inherited={saved}
                           onChange={(patch) => draft.set("defaults", { ...saved, ...Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)) } as VideoOptions)} />
          </Section>
        );
      }}
    </SettingsScreen>
  );
}
