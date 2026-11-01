import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { router } from "./app";
import { AuthGate } from "./lib/auth";
import { queryClient } from "./lib/queries";
import "./index.css";

// A service worker of the first release may still be installed: fetching sw.js replaces it with one that removes itself.
navigator.serviceWorker?.getRegistrations?.().then((registrations) => registrations.forEach((registration) => void registration.update())).catch(() => {});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </QueryClientProvider>
  </StrictMode>,
);
