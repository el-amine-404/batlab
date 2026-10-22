import { createBrowserRouter, Navigate } from "react-router";

import { Layout, SettingsFrame } from "@/components/layout/Layout";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SharePage } from "@/pages/SharePage";

import { PAGES, SETTINGS } from "./navigation";

/** The screens of app/navigation.tsx. The old addresses of Activity and System lead to where they went. */
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      ...PAGES.map((screen) => ({ path: screen.path, element: screen.element })),
      // The phone's Share menu (manifest.webmanifest): opens Add with the link filled in.
      { path: "/share", element: <SharePage /> },
      { path: "/activity", element: <Navigate to="/activity/queue" replace /> },
      { path: "/system/*", element: <Navigate to="/settings/status" replace /> },
      {
        path: "/settings",
        element: <SettingsFrame />,
        children: [
          { index: true, element: <Navigate to={SETTINGS[0].path} replace /> },
          ...SETTINGS.map((screen) => ({ path: screen.path, element: screen.element })),
        ],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
