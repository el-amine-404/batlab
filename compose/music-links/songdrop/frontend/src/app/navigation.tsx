/**
 * Every screen of songdrop, in the order of the sidebar. The router
 * (app/router.tsx) and the sidebar (components/layout/Sidebar.tsx) are both
 * made from this list: a new screen is one page component and one entry here.
 * need: the permission it is shown for (the server refuses the rest anyway).
 * Settings and System load their code when opened.
 */
import {
  Activity,
  Cable,
  CircleGauge,
  Cookie,
  Download,
  FolderCog,
  Gauge,
  History,
  KeyRound,
  type LucideIcon,
  ListMusic,
  Music2,
  Plus,
  RotateCw,
  SearchX,
  Server,
  Settings,
  UserRound,
  Users,
} from "lucide-react";
import { lazy, type ReactNode } from "react";

import type { Permission } from "@/lib/api";
import { AddPage } from "@/pages/AddPage";
import { HistoryPage } from "@/pages/activity/HistoryPage";
import { MissingPage } from "@/pages/activity/MissingPage";
import { QueuePage } from "@/pages/activity/QueuePage";

const AccountPage = lazy(() => import("@/pages/settings/AccountPage"));
const PeoplePage = lazy(() => import("@/pages/settings/PeoplePage"));
const KeysPage = lazy(() => import("@/pages/settings/KeysPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage").then((m) => ({ default: m.GeneralPage })));
const DownloadsPage = lazy(() => import("@/pages/settings/DownloadsPage").then((m) => ({ default: m.DownloadsPage })));
const RetriesPage = lazy(() => import("@/pages/settings/RetriesPage").then((m) => ({ default: m.RetriesPage })));
const ConnectPage = lazy(() => import("@/pages/settings/ConnectPage").then((m) => ({ default: m.ConnectPage })));
const DefaultsPage = lazy(() => import("@/pages/settings/DefaultsPage").then((m) => ({ default: m.DefaultsPage })));
const YouTubePage = lazy(() => import("@/pages/settings/YouTubePage").then((m) => ({ default: m.YouTubePage })));
const StatusPage = lazy(() => import("@/pages/system/StatusPage").then((m) => ({ default: m.StatusPage })));

/** A count shown beside a sidebar entry, from the job list. */
export type Counter = "queue" | "missing";

export type Screen = {
  path: string;
  label: string;
  icon: LucideIcon;
  element: ReactNode;
  counter?: Counter;
  need?: Permission;
};

export type Section = {
  label: string;
  icon: LucideIcon;
  /** Where the section starts: its first screen. */
  path: string;
  screens: Screen[];
};

export const HOME: Screen = { path: "/", label: "Add", icon: Plus, element: <AddPage />, need: "download" };

export const SECTIONS: Section[] = [
  {
    label: "Activity",
    icon: Activity,
    path: "/activity",
    screens: [
      { path: "/activity/queue", label: "Queue", icon: Download, element: <QueuePage />, counter: "queue" },
      { path: "/activity/history", label: "History", icon: History, element: <HistoryPage /> },
      { path: "/activity/missing", label: "Missing", icon: SearchX, element: <MissingPage />, counter: "missing" },
    ],
  },
  {
    label: "Settings",
    icon: Settings,
    path: "/settings",
    screens: [
      { path: "/settings/account", label: "Your account", icon: UserRound, element: <AccountPage /> },
      { path: "/settings/general", label: "General", icon: FolderCog, element: <GeneralPage />, need: "settings" },
      { path: "/settings/downloads", label: "Downloads", icon: Gauge, element: <DownloadsPage />, need: "settings" },
      { path: "/settings/retries", label: "Retries", icon: RotateCw, element: <RetriesPage />, need: "settings" },
      { path: "/settings/connect", label: "Connect", icon: Cable, element: <ConnectPage />, need: "settings" },
      { path: "/settings/spotify", label: "Spotify", icon: Music2, element: <DefaultsPage engine="spotdl" />, need: "settings" },
      { path: "/settings/other-sites", label: "Other sites", icon: ListMusic, element: <DefaultsPage engine="ytdlp" />, need: "settings" },
      { path: "/settings/youtube", label: "YouTube", icon: Cookie, element: <YouTubePage />, need: "settings" },
      { path: "/settings/people", label: "People", icon: Users, element: <PeoplePage />, need: "admin" },
      { path: "/settings/keys", label: "API keys", icon: KeyRound, element: <KeysPage />, need: "admin" },
    ],
  },
  {
    label: "System",
    icon: Server,
    path: "/system",
    screens: [{ path: "/system/status", label: "Status", icon: CircleGauge, element: <StatusPage /> }],
  },
];
