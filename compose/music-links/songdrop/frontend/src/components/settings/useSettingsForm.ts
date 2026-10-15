import { useEffect, useMemo, useState } from "react";
import { useBlocker } from "react-router";
import { toast } from "sonner";

import { api, errorText, type Settings, type SettingsPatch } from "@/lib/api";
import { useLoad } from "@/lib/hooks";

type Editable = keyof SettingsPatch & keyof Settings;

const pick = <K extends Editable>(settings: Settings, keys: readonly K[]) =>
  structuredClone(Object.fromEntries(keys.map((key) => [key, settings[key]]))) as Pick<Settings, K>;

/**
 * One settings screen's form: the saved settings, a draft of the fields this
 * screen edits, and Save, which sends only what changed (PATCH). Leaving the
 * screen with unsaved changes asks first.
 */
export function useSettingsForm<K extends Editable>(keys: readonly K[]) {
  const { data: saved, error, setData } = useLoad(api.settings);
  const [draft, setDraft] = useState<Pick<Settings, K> | null>(null);
  const [saving, setSaving] = useState(false);

  // A new saved version (loaded or just saved) starts a new draft. keys is a
  // constant per screen, so it is left out: a re-render must not reset the draft.
  useEffect(() => {
    if (saved) setDraft(pick(saved, keys));
  }, [saved]);

  const changes = useMemo(() => {
    if (!saved || !draft) return {} as SettingsPatch;
    const patch: Record<string, unknown> = {};
    for (const key of keys) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(saved[key])) patch[key] = draft[key];
    }
    return patch as SettingsPatch;
  }, [saved, draft, keys]);
  const dirty = Object.keys(changes).length > 0;

  const blocker = useBlocker(dirty && !saving);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("Leave without saving your changes?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const set = <F extends K>(key: F, value: Settings[F]) =>
    setDraft((current) => current && { ...current, [key]: value });

  const save = async () => {
    setSaving(true);
    try {
      setData(await api.saveSettings(changes));
      toast.success("Saved", { description: "Used from the next download on." });
    } catch (caught) {
      toast.error("Couldn't save", { description: errorText(caught) });
    } finally {
      setSaving(false);
    }
  };

  const discard = () => saved && setDraft(pick(saved, keys));

  return { saved, draft, error, set, dirty, saving, save, discard };
}

export type SettingsForm = ReturnType<typeof useSettingsForm>;
