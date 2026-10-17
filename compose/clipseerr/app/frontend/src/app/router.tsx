import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { ArtistPage } from "@/pages/ArtistPage";
import { DiscoverPage } from "@/pages/DiscoverPage";
import { GenresPage } from "@/pages/GenresPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReleasesPage } from "@/pages/ReleasesPage";
import { RequestPage } from "@/pages/RequestPage";
import { RequestsPage } from "@/pages/RequestsPage";
import { SearchPage } from "@/pages/SearchPage";
import { GenrePage, ShelfPage } from "@/pages/ShelfPage";
import { WatchPage } from "@/pages/WatchPage";

// Settings are opened now and then: their code loads when they are.
const ServicesPage = lazy(() => import("@/pages/settings/ServicesPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const StatusPage = lazy(() => import("@/pages/settings/StatusPage"));

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <DiscoverPage /> },
      { path: "/releases", element: <ReleasesPage /> },
      { path: "/shelf/releases", element: <Navigate to="/releases" replace /> },
      { path: "/shelf/:id", element: <ShelfPage /> },
      { path: "/genres", element: <GenresPage /> },
      { path: "/genres/:id", element: <GenrePage /> },
      { path: "/search", element: <SearchPage /> },
      { path: "/artist/:id", element: <ArtistPage /> },
      { path: "/watch/:id", element: <WatchPage /> },
      { path: "/requests", element: <RequestsPage /> },
      { path: "/requests/:id", element: <RequestPage /> },
      { path: "/settings", element: <Navigate to="/settings/services" replace /> },
      { path: "/settings/services", element: <ServicesPage /> },
      { path: "/settings/notifications", element: <NotificationsPage /> },
      { path: "/settings/general", element: <GeneralPage /> },
      { path: "/settings/status", element: <StatusPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
