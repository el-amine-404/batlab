import { AlertCircle, Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useBlocker } from "react-router";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/**
 * While a screen has unsaved changes: a bar at the top that says so (with
 * Save and Discard), and a question before leaving the screen or the page,
 * so that changes are never lost without a word.
 */
export function Unsaved({ dirty, busy, onSave, onDiscard, what = "settings" }: {
  dirty: boolean;
  busy: boolean;
  onSave: () => Promise<boolean>;
  onDiscard: () => void;
  what?: string;
}) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);

  useEffect(() => {
    if (!dirty) return;
    // Closing the tab or reloading: the browser's own question.
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <>
      {dirty && (
        <div className="sticky top-14 z-20 -mx-4 mb-4 border-y border-warning/30 bg-[color-mix(in_oklch,var(--warning)_12%,var(--background))] px-4 py-2.5 sm:-mx-6 sm:px-6 lg:top-0 lg:-mx-10 lg:px-10"
             role="status">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
            <AlertCircle className="size-4 shrink-0 text-warning" />
            <p className="flex-1 text-sm">You changed {what}. They apply once saved.</p>
            <Button size="sm" variant="ghost" onClick={onDiscard} disabled={busy}>Discard</Button>
            <Button size="sm" onClick={() => void onSave()} disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save</Button>
          </div>
        </div>
      )}
      <Dialog open={blocker.state === "blocked"} onOpenChange={(open) => !open && blocker.state === "blocked" && blocker.reset()}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Leave without saving?</DialogTitle>
            <DialogDescription>
              The {what} you changed here aren't saved. Leaving this page drops them; the saved values stay.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="justify-end">
            <Button variant="secondary" onClick={() => blocker.state === "blocked" && blocker.reset()}>Stay</Button>
            <Button variant="ghost" className="text-destructive" onClick={() => { onDiscard(); if (blocker.state === "blocked") blocker.proceed(); }}>
              Leave, drop them
            </Button>
            <Button disabled={busy} onClick={async () => { if (await onSave() && blocker.state === "blocked") blocker.proceed(); }}>
              Save and leave
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
