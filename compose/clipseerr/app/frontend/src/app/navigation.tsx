/**
 * The sidebar's pages and the settings' list. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here. need: the
 * permission it is shown for (the server refuses the rest anyway).
 */
import { Bell, Cable, CalendarClock, CircleGauge, Compass, Disc3, Inbox, KeyRound, Library, type LucideIcon, Search,
         SlidersHorizontal, Tags, UserRound, Users } from "lucide-react";

import type { Permission } from "@/lib/api";

export type Entry = { path: string; label: string; icon: LucideIcon; counter?: "active"; need?: Permission };

export const BROWSE: Entry[] = [
  { path: "/", label: "Discover", icon: Compass },
  { path: "/releases", label: "Releases", icon: CalendarClock },
  { path: "/genres", label: "Genres", icon: Disc3 },
  { path: "/search", label: "Search", icon: Search },
  { path: "/tags", label: "Tags", icon: Tags },
  { path: "/requests", label: "Requests", icon: Inbox, counter: "active" },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/account", label: "Your account", icon: UserRound },
  { path: "/settings/services", label: "Services", icon: Cable, need: "settings" },
  { path: "/settings/notifications", label: "Notifications", icon: Bell, need: "settings" },
  { path: "/settings/libraries", label: "Libraries", icon: Library, need: "settings" },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal, need: "settings" },
  { path: "/settings/people", label: "People", icon: Users, need: "admin" },
  { path: "/settings/keys", label: "API keys", icon: KeyRound, need: "admin" },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];

/** Whether this person sees an entry. */
export const shown = (can: (permission: Permission) => boolean) => (entry: Entry) => !entry.need || can(entry.need);
