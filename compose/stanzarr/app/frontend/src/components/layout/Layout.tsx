import { SettingsLayout, Shell, Sidebar, useConnection } from "@batlab/ui";
import { Braces } from "lucide-react";
import { Suspense } from "react";
import { Outlet } from "react-router";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";

import { BROWSE, SETTINGS, shown } from "@/app/navigation";
import { Logo } from "@/components/Logo";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useJobs, useStatus } from "@/lib/queries";

import { Loading } from "./States";

/** The frame of every screen (lib/batlab-ui): stanzarr's pages, the lines to check, the AI jobs under way. */
export function Layout() {
  const live = useConnection();
  const { data: status } = useStatus();
  const { data: jobs } = useJobs();
  const me = useMe();
  const can = useCan();

  const toCheck = status?.to_check.songs ?? 0;
  const running = jobs?.filter((job) => job.status === "running" || job.status === "queued").length ?? 0;
  const badges: Record<string, { count: number; tone?: "active" | "warning"; title: string }> = {
    "/check": { count: toCheck, tone: "warning", title: `${toCheck} songs with AI lines to check` },
    "/queue": { count: running, title: `${running} AI syncs under way` },
  };
  const items = BROWSE.filter(shown(can)).map((entry) => ({ to: entry.path, label: entry.label, icon: entry.icon,
                                                            badge: badges[entry.path] }));

  return (
    <TooltipProvider delayDuration={150}>
    <Shell
      name="stanzarr"
      logo={<Logo className="size-7" />}
      loading={<Loading />}
      sidebar={(close: () => void) => (
        <Sidebar
          name="stanzarr"
          logo={<Logo className="size-8" />}
          items={items}
          settings="/settings"
          links={[{ href: "/docs", label: "API", icon: Braces }]}
          user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
          onSignOut={() => void signOut()}
          version={status?.version}
          status="/settings/status"
          live={live}
          onClose={close}
        />
      )}
    />
    </TooltipProvider>
  );
}

/** The settings' list beside the open one: those this person may open. */
export function SettingsFrame() {
  const can = useCan();
  const entries = SETTINGS.filter(shown(can)).map((entry) => ({ to: entry.path, label: entry.label, icon: entry.icon }));
  return <SettingsLayout entries={entries} loading={<Loading />} />;
}

/** A screen without the sidebar (the editor): its own header says where it is and leads back. */
export function FullScreen() {
  return (
    <TooltipProvider delayDuration={300}>
      <Suspense fallback={<Loading />}>
        <Outlet />
      </Suspense>
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
    </TooltipProvider>
  );
}
