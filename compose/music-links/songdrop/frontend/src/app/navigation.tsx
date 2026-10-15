/**
 * Every screen of songdrop, in the order of the sidebar. The router
 * (app/router.tsx) and the sidebar (components/layout/Sidebar.tsx) are both
 * made from this list: a new screen is one page component and one entry here.
 */
import {
  Activity,
  Cable,
  Cookie,
  Download,
  FolderCog,
  Gauge,
  History,
  type LucideIcon,
  ListMusic,
  Music2,
  Plus,
  RotateCw,
  SearchX,
  Settings,
  Server,
  CircleGauge,
} from "lucide-react";
import type { ReactNode } from "react";

import { AddPage } from "@/pages/AddPage";
import { HistoryPage } from "@/pages/activity/HistoryPage";
import { MissingPage } from "@/pages/activity/MissingPage";
import { QueuePage } from "@/pages/activity/QueuePage";
import { ConnectPage } from "@/pages/settings/ConnectPage";
import { DefaultsPage } from "@/pages/settings/DefaultsPage";
import { DownloadsPage } from "@/pages/settings/DownloadsPage";
import { GeneralPage } from "@/pages/settings/GeneralPage";
import { RetriesPage } from "@/pages/settings/RetriesPage";
import { YouTubePage } from "@/pages/settings/YouTubePage";
import { StatusPage } from "@/pages/system/StatusPage";

/** A count shown beside a sidebar entry, from the job list. */
export type Counter = "queue" | "missing";

export type Screen = {
  path: string;
  label: string;
  icon: LucideIcon;
  element: ReactNode;
  counter?: Counter;
};

export type Section = {
  label: string;
  icon: LucideIcon;
  /** Where the section starts: its first screen. */
  path: string;
  screens: Screen[];
};

export const HOME: Screen = { path: "/", label: "Add", icon: Plus, element: <AddPage /> };

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
      { path: "/settings/general", label: "General", icon: FolderCog, element: <GeneralPage /> },
      { path: "/settings/downloads", label: "Downloads", icon: Gauge, element: <DownloadsPage /> },
      { path: "/settings/retries", label: "Retries", icon: RotateCw, element: <RetriesPage /> },
      { path: "/settings/connect", label: "Connect", icon: Cable, element: <ConnectPage /> },
      { path: "/settings/spotify", label: "Spotify", icon: Music2, element: <DefaultsPage engine="spotdl" /> },
      { path: "/settings/other-sites", label: "Other sites", icon: ListMusic, element: <DefaultsPage engine="ytdlp" /> },
      { path: "/settings/youtube", label: "YouTube", icon: Cookie, element: <YouTubePage /> },
    ],
  },
  {
    label: "System",
    icon: Server,
    path: "/system",
    screens: [{ path: "/system/status", label: "Status", icon: CircleGauge, element: <StatusPage /> }],
  },
];
