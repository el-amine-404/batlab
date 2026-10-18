import { AlertTriangle, Check, ChevronDown, Copy, Home, type LucideIcon, RotateCw, SearchX, WifiOff } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { toClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

export type ErrorKind = "not-found" | "offline" | "broken" | "server";

const KINDS: Record<ErrorKind, { icon: LucideIcon; code: string; title: string; text: string }> = {
  "not-found": { icon: SearchX, code: "404", title: "Not found", text: "There's nothing at this address. It may have been removed, or the link is wrong." },
  offline: { icon: WifiOff, code: "Offline", title: "Can't reach videodrop", text: "Check that you're on the home network, then try again." },
  server: { icon: AlertTriangle, code: "500", title: "videodrop had a problem", text: "The server couldn't answer. The details below say what it reported." },
  broken: { icon: AlertTriangle, code: "Error", title: "This page broke", text: "Something went wrong while showing it. Reloading usually fixes it." },
};

/** Which kind of error a thrown value is. */
export function kindOf(error: unknown): ErrorKind {
  if (error instanceof ApiError) return error.status === 404 ? "not-found" : error.status === 0 ? "offline" : "server";
  return "broken";
}

function details(error: unknown): string {
  if (error instanceof ApiError) return `${error.status || "network"}: ${error.message}`;
  if (error instanceof Error) return `${error.name}: ${error.message}${error.stack ? `\n\n${error.stack}` : ""}`;
  return String(error ?? "");
}

/**
 * A whole screen for what went wrong: what it is in words, what to do, a
 * way back, and the details (copyable) for when it needs reporting.
 */
export function ErrorScreen({ error, kind = kindOf(error), retry, message, className, children }: {
  error?: unknown;
  kind?: ErrorKind;
  retry?: () => void;
  /** Says more than the kind's text, e.g. the server's sentence. */
  message?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = KINDS[kind];
  const Icon = meta.icon;
  const text = error ? details(error) : "";
  const said = message ?? (error instanceof ApiError && kind !== "offline" ? error.message : undefined);

  return (
    <div className={cn("flex min-h-[65dvh] flex-col items-center justify-center px-4 py-12 text-center", className)} role="alert">
      <span className={cn("flex size-14 items-center justify-center rounded-2xl",
                          kind === "not-found" ? "bg-secondary text-muted-foreground" : "bg-destructive/10 text-destructive")}>
        <Icon className="size-7" />
      </span>
      <p className="mt-5 text-xs font-medium tracking-wider text-muted-foreground uppercase">{meta.code}</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">{meta.title}</h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">{meta.text}</p>
      {said && <p className="mt-3 max-w-md rounded-lg border border-border bg-card px-3 py-2 text-sm">{said}</p>}
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {/* Asking again for what isn't there gives the same answer. */}
        {retry && kind !== "not-found" && <Button onClick={retry}><RotateCw /> Try again</Button>}
        {!retry && kind !== "not-found" && <Button onClick={() => window.location.reload()}><RotateCw /> Reload</Button>}
        <Button variant="secondary" asChild><Link to="/"><Home /> Downloads</Link></Button>
        {children}
      </div>
      {text && (
        <div className="mt-8 w-full max-w-xl text-left">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                  className="mx-auto flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} /> Details
          </button>
          {open && (
            <div className="relative mt-2 rounded-lg border border-border bg-black/30">
              <Button variant="ghost" size="icon-sm" className="absolute top-1.5 right-1.5" aria-label="Copy the details"
                      onClick={() => {
                        void toClipboard(`${window.location.href}\n${text}`).then(() => setCopied(true), () => undefined);
                      }}>
                {copied ? <Check className="text-success" /> : <Copy />}
              </Button>
              <pre className="max-h-64 overflow-auto p-3 pr-10 text-xs whitespace-pre-wrap text-muted-foreground">
                {window.location.pathname}{"\n"}{text}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
