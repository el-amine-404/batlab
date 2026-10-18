/**
 * The sidebar's entries. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here.
 */
import { Bell, CircleAlert, CircleGauge, Cookie, Download, FolderOutput, HardDrive, type LucideIcon, ScrollText, Settings2,
         SlidersHorizontal, Zap } from "lucide-react";

export type Entry = { path: string; label: string; icon: LucideIcon };

export const BROWSE: Entry[] = [
  { path: "/", label: "Downloads", icon: Download },
  { path: "/destinations", label: "Destinations", icon: FolderOutput },
  { path: "/issues", label: "Issues", icon: CircleAlert },
];

/** Shown under Settings while one of them is open. */
export const SETTINGS: Entry[] = [
  { path: "/settings/general", label: "General", icon: SlidersHorizontal },
  { path: "/settings/downloads", label: "Downloads", icon: Zap },
  { path: "/settings/options", label: "Default options", icon: Settings2 },
  { path: "/settings/storage", label: "Storage", icon: HardDrive },
  { path: "/settings/cookies", label: "Cookies", icon: Cookie },
  { path: "/settings/notifications", label: "Notifications", icon: Bell },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
  { path: "/settings/log", label: "Log", icon: ScrollText },
];
