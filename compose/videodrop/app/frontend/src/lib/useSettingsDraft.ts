/**
 * Settings being changed on a screen: only the fields changed are kept
 * (the draft), so saving sends only them (PATCH) and another tab's changes
 * to other fields stay. dirty says there is something unsaved.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { api, errorText, type Settings, type SettingsPatch } from "./api";
import { keys, useSettings } from "./queries";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function useSettingsDraft() {
  const client = useQueryClient();
  const query = useSettings();
  const [draft, setDraft] = useState<Partial<Settings>>({});
  const [busy, setBusy] = useState(false);
  const saved = query.data;

  function value<K extends keyof Settings>(key: K): Settings[K] | undefined {
    return key in draft ? (draft[key] as Settings[K]) : saved?.[key];
  }

  function set<K extends keyof Settings>(key: K, next: Settings[K]) {
    setDraft((current) => {
      const { [key]: _old, ...rest } = current;
      return saved && same(next, saved[key]) ? rest : { ...rest, [key]: next };
    });
  }

  const dirty = Object.keys(draft).length > 0;

  async function save(): Promise<boolean> {
    if (!dirty) return true;
    setBusy(true);
    try {
      client.setQueryData<Settings>(keys.settings, await api.saveSettings(draft as SettingsPatch));
      setDraft({});
      toast.success("Settings saved");
      return true;
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't save the settings."));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return { ...query, saved, value, set, dirty, busy, save, discard: () => setDraft({}), changed: Object.keys(draft) as (keyof Settings)[] };
}

export type SettingsDraft = ReturnType<typeof useSettingsDraft>;
