/**
 * The sidebar's pages and the settings' list. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here. need: the
 * permission it is shown for (the server refuses the rest anyway).
 */
import { Bell, CircleAlert, CircleGauge, Cookie, Download, FolderOutput, Globe, HardDrive, KeyRound, type LucideIcon, ScrollText,
         Settings2, SlidersHorizontal, UserRound, Users, Zap } from "lucide-react";

import type { Permission } from "@/lib/api";

export type Entry = { path: string; label: string; icon: LucideIcon; need?: Permission };

export const BROWSE: Entry[] = [
  { path: "/", label: "Downloads", icon: Download },
  { path: "/destinations", label: "Destinations", icon: FolderOutput, need: "settings" },
  { path: "/sites", label: "Sites", icon: Globe },
  { path: "/issues", label: "Issues", icon: CircleAlert, need: "settings" },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/account", label: "Your account", icon: UserRound },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal, need: "settings" },
  { path: "/settings/downloads", label: "Downloads", icon: Zap, need: "settings" },
  { path: "/settings/options", label: "Default options", icon: Settings2, need: "settings" },
  { path: "/settings/storage", label: "Storage", icon: HardDrive, need: "settings" },
  { path: "/settings/cookies", label: "Cookies", icon: Cookie, need: "settings" },
  { path: "/settings/notifications", label: "Notifications", icon: Bell, need: "settings" },
  { path: "/settings/people", label: "People", icon: Users, need: "admin" },
  { path: "/settings/keys", label: "API keys", icon: KeyRound, need: "admin" },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
  { path: "/settings/log", label: "Log", icon: ScrollText, need: "settings" },
];

/** Whether this person sees an entry. */
export const shown = (can: (permission: Permission) => boolean) => (entry: Entry) => !entry.need || can(entry.need);
