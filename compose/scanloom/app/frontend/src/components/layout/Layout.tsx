import { SettingsLayout, Shell, Sidebar, useConnection } from "@batlab/ui";
import { useQuery } from "@tanstack/react-query";
import { Bell, Braces, CloudUpload, History, KeyRound, type LucideIcon, ScanLine, ScrollText, SlidersHorizontal, UserRound, Users } from "lucide-react";
import { lazy, useEffect, useState } from "react";
import { Link, useLocation } from "react-router";

import { Logo } from "@/components/Logo";
import { api, type Permission } from "@/lib/api";
import { signOut, useCan, useMe } from "@/lib/auth";
import { useLiveUpdates } from "@/lib/live";
import { useUploads } from "@/lib/uploads";
import { plural } from "@/lib/utils";

import { Loading } from "./States";

const AccountPage = lazy(() => import("@/pages/settings/AccountPage"));
const PeoplePage = lazy(() => import("@/pages/settings/PeoplePage"));
const KeysPage = lazy(() => import("@/pages/settings/KeysPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const ActivityPage = lazy(() => import("@/pages/settings/ActivityPage"));

export const SETTINGS: { path: string; label: string; icon: LucideIcon; element: React.ReactNode; need?: Permission }[] = [
  { path: "/settings/account", label: "Your account", icon: UserRound, element: <AccountPage /> },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal, element: <GeneralPage />, need: "settings" },
  { path: "/settings/notifications", label: "Notifications", icon: Bell, element: <NotificationsPage />, need: "settings" },
  { path: "/settings/activity", label: "Activity", icon: ScrollText, element: <ActivityPage />, need: "settings" },
  { path: "/settings/people", label: "People", icon: Users, element: <PeoplePage />, need: "admin" },
  { path: "/settings/keys", label: "API keys", icon: KeyRound, element: <KeysPage />, need: "admin" },
];

/** Photos still on their way, wherever one is, and the way back to their scan. */
function UploadsNotice() {
  const uploads = useUploads();
  const { pathname } = useLocation();
  const sending = uploads.filter((item) => item.status !== "failed");
  const project = sending[0]?.project;
  if (!project || pathname.startsWith(`/scans/${project}`)) return null;
  return (
    <Link to={`/scans/${project}`} className="fixed right-4 bottom-[max(1rem,env(safe-area-inset-bottom))] z-40 flex items-center gap-2 rounded-full border border-border bg-popover px-4 py-2.5 text-sm font-medium shadow-2xl hover:bg-accent">
      <CloudUpload className="size-4 text-primary" /> Sending {plural(sending.length, "photo")}
    </Link>
  );
}

export function Layout() {
  useLiveUpdates();
  const live = useConnection("/api/v2/health");
  const me = useMe();
  const [version, setVersion] = useState<string>();
  useEffect(() => { api.health().then((health) => setVersion(health.version), () => setVersion(undefined)); }, []);
  const working = useQuery({ queryKey: ["projects", { status: "processing" }], queryFn: () => api.projects({ limit: 1, offset: 0, status: "processing" }) });
  const count = working.data?.total ?? 0;
  return (
    <>
      <Shell
        name="Scanloom"
        logo={<Logo className="size-7" />}
        width={(pathname) => (pathname.startsWith("/settings") ? "max-w-5xl" : pathname === "/" ? "max-w-4xl" : "max-w-[100rem]")}
        loading={<Loading />}
        sidebar={(close) => (
          <Sidebar
            name="Scanloom"
            logo={<Logo className="size-8" />}
            items={[
              { to: "/", label: "New scan", icon: ScanLine },
              { to: "/scans", label: "Scans", icon: History, badge: count ? { count, title: `${plural(count, "scan")} in progress` } : undefined },
            ]}
            settings="/settings"
            links={[{ href: "/docs", label: "API", icon: Braces }]}
            user={{ name: me.name, role: me.permissions.includes("admin") ? "Admin" : "Member" }}
            account="/settings/account"
            onSignOut={() => void signOut()}
            version={version}
            live={live}
            onClose={close}
          />
        )}
      />
      <UploadsNotice />
    </>
  );
}

export function SettingsFrame() {
  const can = useCan();
  const entries = SETTINGS.filter((screen) => !screen.need || can(screen.need)).map(({ path, label, icon }) => ({ to: path, label, icon }));
  return <SettingsLayout entries={entries} loading={<Loading />} />;
}
