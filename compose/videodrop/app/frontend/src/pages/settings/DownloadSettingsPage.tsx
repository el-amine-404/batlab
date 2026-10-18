import { NumberField, Section, SettingsScreen } from "@/components/settings/fields";

export default function DownloadSettingsPage() {
  return (
    <SettingsScreen title="Downloads" description="How many run at once, how fast, and what happens when one fails.">
      {(draft) => (
        <>
          <Section title="At once" description="Each download is a process of its own: more at once use more memory and bandwidth.">
            <div className="grid gap-5 sm:grid-cols-2">
              <NumberField draft={draft} name="max_parallel" label="Downloads at once" min={1} max={16} help="Every site together." />
              <NumberField draft={draft} name="per_site_parallel" label="From one site at once" min={1} max={16}
                           help="Every app here shares one internet address; YouTube asks an address that downloads too much to sign in." />
            </div>
          </Section>
          <Section title="Speed">
            <NumberField draft={draft} name="rate_limit_mib" label="Speed limit per download" unit="MiB/s" min={0} max={10000} step={0.5}
                         help="To leave room for the rest of the house. 0: as fast as it goes." />
          </Section>
          <Section title="When one fails">
            <NumberField draft={draft} name="retries" label="Tries again" unit="times" min={0} max={10}
                         help="Only for failures that may pass (the network, the site busy), after 1 min, 5 min, 25 min… A video that is gone isn't tried again." />
          </Section>
        </>
      )}
    </SettingsScreen>
  );
}
