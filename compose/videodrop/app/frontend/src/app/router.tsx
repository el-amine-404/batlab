import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { RouteError } from "@/components/layout/RouteError";
import { DownloadsPage } from "@/pages/DownloadsPage";
import { JobPage } from "@/pages/JobPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

// Opened now and then: their code loads when they are.
const DestinationsPage = lazy(() => import("@/pages/DestinationsPage"));
const IssuesPage = lazy(() => import("@/pages/IssuesPage"));
const SitesPage = lazy(() => import("@/pages/SitesPage"));
const PlayerPage = lazy(() => import("@/pages/PlayerPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const DownloadSettingsPage = lazy(() => import("@/pages/settings/DownloadSettingsPage"));
const DefaultOptionsPage = lazy(() => import("@/pages/settings/DefaultOptionsPage"));
const StoragePage = lazy(() => import("@/pages/settings/StoragePage"));
const CookiesPage = lazy(() => import("@/pages/settings/CookiesPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const StatusPage = lazy(() => import("@/pages/settings/StatusPage"));
const LogPage = lazy(() => import("@/pages/settings/LogPage"));
const AccountPage = lazy(() => import("@/pages/settings/AccountPage"));
const PeoplePage = lazy(() => import("@/pages/settings/PeoplePage"));
const KeysPage = lazy(() => import("@/pages/settings/KeysPage"));
const SharePage = lazy(() => import("@/pages/SharePage"));

export const router = createBrowserRouter([
  {
    element: <Layout />,
    // The layout itself breaking: the whole page.
    errorElement: <RouteError />,
    children: [
      {
        // A screen breaking: only it is replaced, the sidebar stays.
        errorElement: <RouteError />,
        children: [
          { path: "/", element: <DownloadsPage /> },
          { path: "/jobs/:id", element: <JobPage /> },
          { path: "/jobs/:id/play", element: <PlayerPage /> },
          { path: "/destinations", element: <DestinationsPage /> },
          { path: "/issues", element: <IssuesPage /> },
          { path: "/sites", element: <SitesPage /> },
          { path: "/share", element: <SharePage /> },
          { path: "/settings", element: <Navigate to="/settings/account" replace /> },
          { path: "/settings/account", element: <AccountPage /> },
          { path: "/settings/people", element: <PeoplePage /> },
          { path: "/settings/keys", element: <KeysPage /> },
          { path: "/settings/general", element: <GeneralPage /> },
          { path: "/settings/downloads", element: <DownloadSettingsPage /> },
          { path: "/settings/options", element: <DefaultOptionsPage /> },
          { path: "/settings/storage", element: <StoragePage /> },
          { path: "/settings/cookies", element: <CookiesPage /> },
          { path: "/settings/notifications", element: <NotificationsPage /> },
          { path: "/settings/status", element: <StatusPage /> },
          { path: "/settings/log", element: <LogPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
