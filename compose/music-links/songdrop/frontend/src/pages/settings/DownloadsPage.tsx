import { isWhole, NumberSetting, SettingGroup } from "@/components/settings/rows";
import { SettingsScreen } from "@/components/settings/SettingsScreen";
import { useSettingsForm } from "@/components/settings/useSettingsForm";

const KEYS = ["concurrent_jobs", "spotdl_parallel", "max_items", "job_timeout_minutes", "retention_hours"] as const;

export function DownloadsPage() {
  const form = useSettingsForm(KEYS);
  const { saved, draft, set } = form;
  const limit = (key: (typeof KEYS)[number]) => (saved?.limits[key] as number[]) ?? [0, 0];
  const invalid = !!draft && KEYS.some((key) => !isWhole(draft[key], limit(key)));

  return (
    <SettingsScreen
      title="Downloads"
      description="How much songdrop does at once, and how long it keeps what it downloaded."
      form={form}
      ready={!!draft}
      invalid={invalid}
    >
      {saved && draft && (
        <>
          <SettingGroup title="At once">
            <NumberSetting id="concurrent-jobs" label="Links at once" unit="links" help="How many links download at the same time; the others wait in the Queue." value={draft.concurrent_jobs} range={limit("concurrent_jobs")} onChange={(v) => set("concurrent_jobs", v)} />
            <NumberSetting id="spotdl-parallel" label="Spotify songs at once" unit="songs" help="Per Spotify link. More is faster, and more likely to be refused by YouTube." value={draft.spotdl_parallel} range={limit("spotdl_parallel")} onChange={(v) => set("spotdl_parallel", v)} />
          </SettingGroup>
          <SettingGroup title="Limits">
            <NumberSetting id="max-items" label="Most songs per link" unit="songs" help="A longer playlist is refused rather than half done." value={draft.max_items} range={limit("max_items")} onChange={(v) => set("max_items", v)} />
            <NumberSetting id="job-timeout" label="Time limit per link" unit="minutes" help="A download still running then is stopped." value={draft.job_timeout_minutes} range={limit("job_timeout_minutes")} onChange={(v) => set("job_timeout_minutes", v)} />
          </SettingGroup>
          <SettingGroup title="Keeping">
            <NumberSetting id="retention" label="Keep downloads for" unit="hours" help="Then a download and its files leave History. Copies in the output folder stay." value={draft.retention_hours} range={limit("retention_hours")} onChange={(v) => set("retention_hours", v)} />
          </SettingGroup>
        </>
      )}
    </SettingsScreen>
  );
}
