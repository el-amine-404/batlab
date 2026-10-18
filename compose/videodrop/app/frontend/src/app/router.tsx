import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { RouteError } from "@/components/layout/RouteError";
import { DownloadsPage } from "@/pages/DownloadsPage";
import { JobPage } from "@/pages/JobPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

// Opened now and then: their code loads when they are.
const DestinationsPage = lazy(() => import("@/pages/DestinationsPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const CookiesPage = lazy(() => import("@/pages/settings/CookiesPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const StatusPage = lazy(() => import("@/pages/settings/StatusPage"));

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
          { path: "/destinations", element: <DestinationsPage /> },
          { path: "/settings", element: <Navigate to="/settings/general" replace /> },
          { path: "/settings/general", element: <GeneralPage /> },
          { path: "/settings/cookies", element: <CookiesPage /> },
          { path: "/settings/notifications", element: <NotificationsPage /> },
          { path: "/settings/status", element: <StatusPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
