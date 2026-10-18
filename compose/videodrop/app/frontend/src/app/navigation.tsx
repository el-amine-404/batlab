/**
 * The sidebar's entries. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here.
 */
import { Bell, CircleGauge, Cookie, Download, FolderOutput, type LucideIcon, SlidersHorizontal } from "lucide-react";

export type Entry = { path: string; label: string; icon: LucideIcon };

export const BROWSE: Entry[] = [
  { path: "/", label: "Downloads", icon: Download },
  { path: "/destinations", label: "Destinations", icon: FolderOutput },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/general", label: "General", icon: SlidersHorizontal },
  { path: "/settings/cookies", label: "Cookies", icon: Cookie },
  { path: "/settings/notifications", label: "Notifications", icon: Bell },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];
