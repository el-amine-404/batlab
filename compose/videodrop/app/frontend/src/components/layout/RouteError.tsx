import { useEffect } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router";

import { isStaleCode, reloadForUpdate } from "@/lib/reload";

import { ErrorScreen } from "./ErrorScreen";

/**
 * A screen that broke: after an update, a tab still on the old version
 * reloads into the new one by itself; anything else gets the error screen,
 * instead of the router's page for developers.
 */
export function RouteError() {
  const error = useRouteError();
  const stale = isStaleCode(error);
  useEffect(() => {
    if (stale) reloadForUpdate();
  }, [stale]);

  if (stale) return <ErrorScreen kind="broken" message="videodrop was updated: this tab is getting the new version." />;
  if (isRouteErrorResponse(error)) {
    return <ErrorScreen kind={error.status === 404 ? "not-found" : "server"} error={new Error(`${error.status} ${error.statusText}`)} />;
  }
  return <ErrorScreen error={error} kind="broken" />;
}
