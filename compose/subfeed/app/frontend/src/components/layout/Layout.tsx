import { SettingsLayout, Shell, Sidebar } from "@batlab/ui";
import { Braces } from "lucide-react";

import { BROWSE, SETTINGS, shown } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useFeeds, useStatus } from "@/lib/queries";

import { Loading } from "./States";

/** The frame of every screen (lib/batlab-ui): subfeed's pages, and the failing feeds beside Feeds. */
export function Layout() {
  const { data: status } = useStatus();
  const { data: feeds } = useFeeds();
  const failing = feeds?.feeds.filter((feed) => feed.enabled && feed.failures > 0).length ?? 0;
  const me = useMe();
  const can = useCan();

  const items = BROWSE.filter(shown(can)).map((entry) => ({
    to: entry.path,
    label: entry.label,
    icon: entry.icon,
    badge: entry.path === "/" ? { count: failing, tone: "error" as const, title: `${failing} failing` } : undefined,
  }));

  return (
    <Shell
      name="subfeed"
      logo={<Logo className="size-7" />}
      loading={<Loading />}
      sidebar={(close) => (
        <Sidebar
          name="subfeed"
          logo={<Logo className="size-8" />}
          items={items}
          settings="/settings"
          links={[{ href: "/docs", label: "API", icon: Braces }]}
          user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
          onSignOut={() => void signOut()}
          version={status?.version}
          status="/settings/status"
          onClose={close}
        />
      )}
    />
  );
}

/** The settings' list beside the open one: those this person may open. */
export function SettingsFrame() {
  const can = useCan();
  const entries = SETTINGS.filter(shown(can)).map((entry) => ({ to: entry.path, label: entry.label, icon: entry.icon }));
  return <SettingsLayout entries={entries} loading={<Loading />} />;
}
