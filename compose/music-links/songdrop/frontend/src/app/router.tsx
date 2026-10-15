import { createBrowserRouter, Navigate } from "react-router";

import { Layout } from "@/components/layout/Layout";
import { NotFoundPage } from "@/pages/NotFoundPage";

import { HOME, SECTIONS } from "./navigation";

/** The screens of app/navigation.tsx; a section's own path opens its first screen. */
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: HOME.path, element: HOME.element },
      ...SECTIONS.flatMap((section) => [
        { path: section.path, element: <Navigate to={section.screens[0].path} replace /> },
        ...section.screens.map((screen) => ({ path: screen.path, element: screen.element })),
      ]),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
