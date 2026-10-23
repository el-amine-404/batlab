/**
 * The sidebar's pages and the settings' list. The router (app/router.tsx) has
 * the screens; a new screen is one page component, one route, and one entry
 * here. need: the permission it is shown for (the server refuses the rest anyway).
 */
import { Activity, Bell, Cable, CircleGauge, Cpu, KeyRound, Library, ListChecks, ListOrdered, type LucideIcon, Search,
         SlidersHorizontal, UserRound, Users } from "lucide-react";

import type { Permission } from "@/lib/api";

export type Entry = { path: string; label: string; icon: LucideIcon; need?: Permission };

export const BROWSE: Entry[] = [
  { path: "/", label: "Find lyrics", icon: Search },
  { path: "/library", label: "Library", icon: Library },
  { path: "/check", label: "To check", icon: ListChecks },
  { path: "/activity", label: "Activity", icon: Activity },
  { path: "/queue", label: "AI queue", icon: ListOrdered },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/account", label: "Your account", icon: UserRound },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal, need: "settings" },
  { path: "/settings/sources", label: "Sources", icon: Cable, need: "settings" },
  { path: "/settings/workers", label: "Workers", icon: Cpu },
  { path: "/settings/notifications", label: "Notifications", icon: Bell, need: "settings" },
  { path: "/settings/people", label: "People", icon: Users, need: "admin" },
  { path: "/settings/keys", label: "API keys", icon: KeyRound, need: "admin" },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];

/** Whether this person sees an entry. */
export const shown = (can: (permission: Permission) => boolean) => (entry: Entry) => !entry.need || can(entry.need);
