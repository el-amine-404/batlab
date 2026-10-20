/**
 * The sidebar's entries. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here. need: the
 * permission it is shown for (the server refuses the rest anyway).
 */
import { Bell, CircleGauge, KeyRound, List, type LucideIcon, Rss, SlidersHorizontal, UserRound, Users } from "lucide-react";

import type { Permission } from "@/lib/api";

export type Entry = { path: string; label: string; icon: LucideIcon; need?: Permission };

export const BROWSE: Entry[] = [
  { path: "/", label: "Feeds", icon: Rss },
  { path: "/posts", label: "All posts", icon: List },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/account", label: "Your account", icon: UserRound },
  { path: "/settings/notifications", label: "Notifications", icon: Bell, need: "settings" },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal, need: "settings" },
  { path: "/settings/people", label: "People", icon: Users, need: "admin" },
  { path: "/settings/keys", label: "API keys", icon: KeyRound, need: "admin" },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];
