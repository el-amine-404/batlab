import { AlertTriangle, RotateCw } from "lucide-react";
import { useEffect } from "react";
import { Link, useRouteError } from "react-router";

import { Button } from "@/components/ui/button";
import { isStaleCode, reloadForUpdate } from "@/lib/reload";

/**
 * A screen that broke: after an update, a tab still on the old version
 * reloads into the new one by itself; anything else says what happened and
 * offers a way back, instead of the router's page for developers.
 */
export function RouteError() {
  const error = useRouteError();
  const stale = isStaleCode(error);
  useEffect(() => {
    if (stale) reloadForUpdate();
  }, [stale]);

  return (
    <div className="flex min-h-[60dvh] flex-col items-center justify-center gap-4 px-6 text-center" role="alert">
      <span className="flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertTriangle className="size-6" />
      </span>
      <div>
        <p className="font-medium">{stale ? "clipseerr was updated" : "This page broke"}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          {stale ? "This tab still had the previous version. Reloading gets the new one."
            : error instanceof Error ? error.message : "Something went wrong while showing it."}
        </p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => window.location.reload()}><RotateCw /> Reload</Button>
        <Button size="sm" variant="ghost" asChild><Link to="/">Back to Discover</Link></Button>
      </div>
    </div>
  );
}
