import { lazy } from "react";
import { createBrowserRouter, Navigate, useParams } from "react-router";

import { FullScreen, Layout, SettingsFrame } from "@/components/layout/Layout";
import { RouteError } from "@/components/layout/RouteError";
import { CheckPage } from "@/pages/CheckPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { QueuePage } from "@/pages/QueuePage";
import { SongPage } from "@/pages/SongPage";

// Opened now and then, or heavy (the editor): their code loads when they are.
const EditorPage = lazy(() => import("@/pages/EditorPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const SourcesPage = lazy(() => import("@/pages/settings/SourcesPage"));
const WorkersPage = lazy(() => import("@/pages/settings/WorkersPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const StatusPage = lazy(() => import("@/pages/settings/StatusPage"));
const AccountPage = lazy(() => import("@/pages/settings/AccountPage"));
const PeoplePage = lazy(() => import("@/pages/settings/PeoplePage"));
const KeysPage = lazy(() => import("@/pages/settings/KeysPage"));

function OldEditor() {
  const { id } = useParams();
  return <Navigate to={`/songs/${id}/edit`} replace />;
}

export const router = createBrowserRouter([
  {
    // The editor: the whole screen, nothing else, for timing by ear.
    element: <FullScreen />,
    errorElement: <RouteError />,
    children: [{ path: "/songs/:id/edit", element: <EditorPage /> }],
  },
  {
    element: <Layout />,
    // The layout itself breaking: the whole page.
    errorElement: <RouteError />,
    children: [
      {
        // A screen breaking: only it is replaced, the sidebar stays.
        errorElement: <RouteError />,
        children: [
          { path: "/", element: <LibraryPage /> },
          { path: "/check", element: <CheckPage /> },
          { path: "/queue", element: <QueuePage /> },
          { path: "/songs/:id", element: <SongPage /> },
          // The page before 1.0: its addresses lead to where those screens are now.
          { path: "/editor/:id", element: <OldEditor /> },
          { path: "/library", element: <Navigate to="/" replace /> },
          { path: "/workers", element: <Navigate to="/settings/workers" replace /> },
          {
            path: "/settings",
            element: <SettingsFrame />,
            children: [
              { index: true, element: <Navigate to="/settings/account" replace /> },
              { path: "account", element: <AccountPage /> },
              { path: "general", element: <GeneralPage /> },
              { path: "sources", element: <SourcesPage /> },
              { path: "workers", element: <WorkersPage /> },
              { path: "notifications", element: <NotificationsPage /> },
              { path: "people", element: <PeoplePage /> },
              { path: "keys", element: <KeysPage /> },
              { path: "status", element: <StatusPage /> },
            ],
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
