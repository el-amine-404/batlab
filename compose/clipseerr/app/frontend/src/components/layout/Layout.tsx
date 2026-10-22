import { SettingsLayout, Shell, Sidebar, useConnection } from "@batlab/ui";
import { Braces } from "lucide-react";

import { BROWSE, SETTINGS, shown } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useActiveCount, useStatus } from "@/lib/queries";

import { SearchBox } from "./SearchBox";
import { Loading } from "./States";

/**
 * The frame of every screen (lib/batlab-ui): clipseerr's pages, the requests
 * under way beside Requests, and the search over every screen. Back finds a
 * long list where it was left (its pages are cached).
 */
export function Layout() {
  const live = useConnection();
  const { data: active } = useActiveCount();
  const { data: status } = useStatus();
  const me = useMe();
  const can = useCan();

  const items = BROWSE.filter(shown(can)).map((entry) => ({
    to: entry.path,
    label: entry.label,
    icon: entry.icon,
    badge: entry.counter ? { count: active ?? 0, title: `${active} under way` } : undefined,
  }));

  return (
    <Shell
      name="clipseerr"
      logo={<Logo className="size-8" />}
      header={<SearchBox className="max-w-xl" />}
      width={(pathname) => (pathname.startsWith("/settings") ? "max-w-6xl" : "max-w-7xl")}
      loading={<Loading />}
      sidebar={(close) => (
        <Sidebar
          name="clipseerr"
          logo={<Logo className="size-8" />}
          items={items}
          settings="/settings"
          links={[{ href: "/api/docs", label: "API", icon: Braces }]}
          user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
          onSignOut={() => void signOut()}
          version={status?.version}
          versionTitle="Status, and the versions it runs"
          status="/settings/status"
          live={live}
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
