import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { RouteError } from "@/components/layout/RouteError";
import { AlbumPage } from "@/pages/AlbumPage";
import { ArtistPage } from "@/pages/ArtistPage";
import { DiscoverPage } from "@/pages/DiscoverPage";
import { GenresPage } from "@/pages/GenresPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReleasesPage } from "@/pages/ReleasesPage";
import { RequestPage } from "@/pages/RequestPage";
import { RequestsPage } from "@/pages/RequestsPage";
import { SearchPage } from "@/pages/SearchPage";
import { TagsPage } from "@/pages/TagsPage";
import { GenrePage, HotArtistsPage, ShelfPage } from "@/pages/ShelfPage";
import { WatchPage } from "@/pages/WatchPage";

// Settings are opened now and then: their code loads when they are.
const ServicesPage = lazy(() => import("@/pages/settings/ServicesPage"));
const NotificationsPage = lazy(() => import("@/pages/settings/NotificationsPage"));
const LibrariesPage = lazy(() => import("@/pages/settings/LibrariesPage"));
const GeneralPage = lazy(() => import("@/pages/settings/GeneralPage"));
const StatusPage = lazy(() => import("@/pages/settings/StatusPage"));
const AccountPage = lazy(() => import("@/pages/settings/AccountPage"));
const PeoplePage = lazy(() => import("@/pages/settings/PeoplePage"));
const KeysPage = lazy(() => import("@/pages/settings/KeysPage"));

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
      { path: "/", element: <DiscoverPage /> },
      { path: "/releases", element: <ReleasesPage /> },
      { path: "/shelf/releases", element: <Navigate to="/releases" replace /> },
      { path: "/shelf/:id", element: <ShelfPage /> },
      { path: "/genres", element: <GenresPage /> },
      { path: "/genres/hot/artists", element: <HotArtistsPage /> },
      { path: "/genres/:id", element: <GenrePage /> },
      { path: "/tags", element: <TagsPage /> },
      { path: "/search", element: <SearchPage /> },
      { path: "/artist/:id", element: <ArtistPage /> },
      { path: "/album/:id", element: <AlbumPage /> },
      { path: "/watch/:id", element: <WatchPage /> },
      { path: "/requests", element: <RequestsPage /> },
      { path: "/requests/:id", element: <RequestPage /> },
      { path: "/settings", element: <Navigate to="/settings/account" replace /> },
      { path: "/settings/account", element: <AccountPage /> },
      { path: "/settings/people", element: <PeoplePage /> },
      { path: "/settings/keys", element: <KeysPage /> },
      { path: "/settings/services", element: <ServicesPage /> },
      { path: "/settings/notifications", element: <NotificationsPage /> },
      { path: "/settings/libraries", element: <LibrariesPage /> },
      { path: "/settings/general", element: <GeneralPage /> },
      { path: "/settings/status", element: <StatusPage /> },
      { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
