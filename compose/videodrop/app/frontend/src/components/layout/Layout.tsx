import { type Badge, SettingsLayout, Shell, Sidebar } from "@batlab/ui";
import { Braces } from "lucide-react";

import { BROWSE, SETTINGS, shown } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useCounts, useIssues, useLiveJobs, useStatus } from "@/lib/queries";

import { Loading } from "./States";

/**
 * The frame of every screen (lib/batlab-ui): videodrop's pages, what runs
 * beside Downloads, what to look at beside Issues. The live stream of
 * downloads is opened here, once.
 */
export function Layout() {
  const live = useLiveJobs();
  const { data: status } = useStatus();
  const { data: counts } = useCounts();
  const me = useMe();
  const can = useCan();
  const { data: issues } = useIssues(can("settings"));

  const active = counts ? counts.queued + counts.running : 0;
  const problems = issues ? issues.counts.error + issues.counts.warning : 0;
  const badges: Record<string, Badge> = {
    "/": { count: active, title: `${active} running or queued` },
    "/issues": { count: problems, tone: issues?.counts.error ? "error" : "warning", title: `${problems} to look at` },
  };
  const items = BROWSE.filter(shown(can)).map((entry) => ({ to: entry.path, label: entry.label, icon: entry.icon, badge: badges[entry.path] }));

  return (
    <Shell
      name="videodrop"
      logo={<Logo className="size-7" />}
      loading={<Loading />}
      sidebar={(close) => (
        <Sidebar
          name="videodrop"
          logo={<Logo className="size-8" />}
          items={items}
          settings="/settings"
          links={[{ href: "/docs", label: "API", icon: Braces }]}
          user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
          onSignOut={() => void signOut()}
          version={status?.version}
          versionTitle={status ? `Status, and the versions it runs: yt-dlp ${status.yt_dlp}` : undefined}
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
