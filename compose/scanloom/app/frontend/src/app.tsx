import { createBrowserRouter, Link, Navigate, useParams } from "react-router";

import { Layout, SETTINGS, SettingsFrame } from "@/components/layout/Layout";
import { Empty } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { ArrangePage } from "@/pages/ArrangePage";
import { HomePage } from "@/pages/HomePage";
import { ProjectPage } from "@/pages/ProjectPage";
import { ScansPage } from "@/pages/ScansPage";
import { FileQuestion } from "lucide-react";

function OldAddress() {
  const { id = "" } = useParams();
  return <Navigate to={`/scans/${id}`} replace />;
}

function NotFound() {
  return (
    <Empty icon={FileQuestion} title="There's nothing here">
      <Button asChild variant="secondary" className="mt-3"><Link to="/">New scan</Link></Button>
    </Empty>
  );
}

export const router = createBrowserRouter([{
  element: <Layout />,
  children: [
    { path: "/", element: <HomePage /> },
    { path: "/scans", element: <ScansPage /> },
    { path: "/scans/:id", element: <ProjectPage /> },
    { path: "/scans/:id/arrange", element: <ArrangePage /> },
    { path: "/p/:id", element: <OldAddress /> },
    {
      path: "/settings",
      element: <SettingsFrame />,
      children: [
        { index: true, element: <Navigate to={SETTINGS[0].path} replace /> },
        ...SETTINGS.map(({ path, element }) => ({ path, element })),
      ],
    },
    { path: "*", element: <NotFound /> },
  ],
}]);
