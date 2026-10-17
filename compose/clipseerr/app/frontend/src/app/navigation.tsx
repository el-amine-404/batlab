/**
 * The sidebar's entries. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here.
 */
import { Cable, CircleGauge, Compass, Disc3, Inbox, type LucideIcon, Search, Bell, SlidersHorizontal } from "lucide-react";

export type Entry = { path: string; label: string; icon: LucideIcon; counter?: "active" };

export const BROWSE: Entry[] = [
  { path: "/", label: "Discover", icon: Compass },
  { path: "/genres", label: "Genres", icon: Disc3 },
  { path: "/search", label: "Search", icon: Search },
  { path: "/requests", label: "Requests", icon: Inbox, counter: "active" },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/services", label: "Services", icon: Cable },
  { path: "/settings/notifications", label: "Notifications", icon: Bell },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];
