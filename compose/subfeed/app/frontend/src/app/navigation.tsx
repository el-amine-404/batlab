/**
 * The sidebar's entries. The router (app/router.tsx) has the screens; a new
 * screen is one page component, one route, and one entry here.
 */
import { Bell, CircleGauge, List, type LucideIcon, Rss, SlidersHorizontal } from "lucide-react";

export type Entry = { path: string; label: string; icon: LucideIcon };

export const BROWSE: Entry[] = [
  { path: "/", label: "Feeds", icon: Rss },
  { path: "/posts", label: "All posts", icon: List },
];

export const SETTINGS: Entry[] = [
  { path: "/settings/notifications", label: "Notifications", icon: Bell },
  { path: "/settings/general", label: "General", icon: SlidersHorizontal },
  { path: "/settings/status", label: "Status", icon: CircleGauge },
];
