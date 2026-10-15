import { useState } from "react";

import { FolderPicker } from "@/components/settings/FolderPicker";
import { SettingGroup, SettingRow } from "@/components/settings/rows";
import { SettingsScreen } from "@/components/settings/SettingsScreen";
import { useSettingsForm } from "@/components/settings/useSettingsForm";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

const KEYS = ["deliver", "output_folder", "public_url"] as const;

export function GeneralPage() {
  const form = useSettingsForm(KEYS);
  const { saved, draft, set } = form;
  const [folderOk, setFolderOk] = useState(true);

  return (
    <SettingsScreen
      title="General"
      description="Where finished songs go, and the address of this page."
      form={form}
      ready={!!draft}
      invalid={!!draft?.deliver && !folderOk}
    >
      {saved && draft && (
        <>
          <SettingGroup title="Output">
            <SettingRow label="Send songs to the folder" htmlFor="deliver" help="Off: songs are only downloadable from History.">
              <Switch id="deliver" className="mt-2" checked={draft.deliver} onCheckedChange={(value) => set("deliver", value)} />
            </SettingRow>
            <SettingRow
              label="Output folder"
              htmlFor="output-folder"
              help={
                <>
                  Each finished song is copied here. In this stack, <code className="font-mono">music/songdrop</code> is
                  musicguard's inbox, which checks songs and adds them to your library.
                </>
              }
            >
              <FolderPicker
                id="output-folder"
                root={saved.output_root}
                value={draft.output_folder}
                onChange={(value) => set("output_folder", value)}
                onValidity={setFolderOk}
                disabled={!draft.deliver}
              />
            </SettingRow>
          </SettingGroup>

          <SettingGroup title="This page">
            <SettingRow
              label="Page address"
              htmlFor="public-url"
              help="Messages sent through Connect link back to the page with it. Empty: no link."
            >
              <Input
                id="public-url"
                value={draft.public_url}
                onChange={(event) => set("public_url", event.target.value)}
                placeholder="http://songdrop.homelab.lan"
                spellCheck={false}
              />
            </SettingRow>
          </SettingGroup>
        </>
      )}
    </SettingsScreen>
  );
}
