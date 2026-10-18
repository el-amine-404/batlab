import { NumberField, Section, SettingsScreen } from "@/components/settings/fields";

export default function StoragePage() {
  return (
    <SettingsScreen title="Storage" description="What may be downloaded, and what is kept.">
      {(draft) => (
        <>
          <Section title="The disk">
            <div className="grid gap-5 sm:grid-cols-2">
              <NumberField draft={draft} name="min_free_gb" label="Keep free on the media disk" unit="GB" min={0} max={10000}
                           help="Downloads wait while there is less; you're told once (Issues, notifications)." />
              <NumberField draft={draft} name="max_filesize_gb" label="Largest video" unit="GB" min={0} max={1000} step={0.5}
                           help="Bigger ones aren't downloaded. 0: no limit." />
            </div>
          </Section>
          <Section title="Files kept here" description="Downloads saved nowhere: their files wait in videodrop for whoever asked.">
            <NumberField draft={draft} name="hold_hours" label="Keep them for" unit="hours" min={1} max={720}
                         help="Then they are deleted; the download stays in the list." />
          </Section>
        </>
      )}
    </SettingsScreen>
  );
}
