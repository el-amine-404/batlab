import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { RouteError } from "@/components/layout/RouteError";
import { FeedPage } from "@/pages/FeedPage";
import { FeedsPage } from "@/pages/FeedsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PostsPage } from "@/pages/PostsPage";

// Settings are opened now and then: their code loads when they are.
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
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
          { path: "/", element: <FeedsPage /> },
          { path: "/feeds/:slug", element: <FeedPage /> },
          { path: "/posts", element: <PostsPage /> },
          { path: "/settings", element: <Navigate to="/settings/notifications" replace /> },
          { path: "/settings/notifications", element: <NotificationsPage /> },
          { path: "/settings/general", element: <GeneralPage /> },
          { path: "/settings/status", element: <StatusPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
