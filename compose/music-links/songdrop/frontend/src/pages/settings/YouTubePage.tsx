import { CookiesCard } from "@/components/settings/CookiesCard";
import { SettingsScreen } from "@/components/settings/SettingsScreen";

export function YouTubePage() {
  return (
    <SettingsScreen
      title="YouTube"
      description="Some videos are shown only to a signed-in viewer (age-restricted ones, members-only…). songdrop can download them with the cookies of a YouTube account. Changes apply at once."
      ready
    >
      <CookiesCard />
    </SettingsScreen>
  );
}
