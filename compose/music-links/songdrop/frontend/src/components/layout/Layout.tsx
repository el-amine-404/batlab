import { type Badge, SettingsLayout, Shell, Sidebar } from "@batlab/ui";
import { BookOpen, Braces } from "lucide-react";
import { useEffect, useState } from "react";

import { type Counter, PAGES, SETTINGS, shown } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { api } from "@/lib/api";
import { signOut, useCan, useMe } from "@/lib/auth";
import { JobsProvider, useJobs } from "@/lib/jobs";

import { Loading } from "./States";

/** The frame of every screen (lib/batlab-ui), inside the job list that gives the sidebar its counts. */
export function Layout() {
  return (
    <JobsProvider>
      <Frame />
    </JobsProvider>
  );
}

function Frame() {
  const { queue, missing } = useJobs();
  const me = useMe();
  const can = useCan();
  const [version, setVersion] = useState<string>();
  useEffect(() => {
    api.health().then((health) => setVersion(health.version), () => setVersion(undefined));
  }, []);

  const badges: Record<Counter, Badge> = {
    queue: { count: queue.length, title: `${queue.length} queued or downloading` },
    missing: { count: missing.length, tone: "warning", title: `${missing.length} songs not found` },
  };
  const items = PAGES.filter(shown(can)).map((screen) => ({
    to: screen.path,
    label: screen.label,
    icon: screen.icon,
    badge: screen.counter && badges[screen.counter],
  }));

  return (
    <Shell
      name="songdrop"
      logo={<Logo className="size-7" />}
      width={(pathname) => (pathname.startsWith("/settings") ? "max-w-5xl" : "max-w-4xl")}
      loading={<Loading />}
      sidebar={(close) => (
        <Sidebar
          name="songdrop"
          logo={<Logo className="size-8" />}
          items={items}
          settings="/settings"
          links={[
            { href: "/docs/", label: "Docs", icon: BookOpen },
            { href: "/docs/api/", label: "API", icon: Braces },
          ]}
          user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
          onSignOut={() => void signOut()}
          version={version}
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
  const entries = SETTINGS.filter(shown(can)).map((screen) => ({ to: screen.path, label: screen.label, icon: screen.icon }));
  return <SettingsLayout entries={entries} loading={<Loading />} />;
}
