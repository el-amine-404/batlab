import { AlertTriangle, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { errorText } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground" role="status">
      <Loader2 className="size-4 animate-spin" /> {label}…
    </div>
  );
}

export function Failed({ error, retry, className }: { error: unknown; retry?: () => void; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center gap-3 py-20 text-center", className)} role="alert">
      <span className="flex size-11 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertTriangle className="size-5" />
      </span>
      <p className="max-w-sm text-sm text-muted-foreground">{errorText(error, "Couldn't load this.")}</p>
      {retry && (
        <Button variant="secondary" size="sm" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border px-6 py-16 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground [&_svg]:size-5">
        {icon}
      </span>
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-sm text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, description, actions, eyebrow }: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="mb-1 text-xs font-medium tracking-wider text-orange-300/90 uppercase">{eyebrow}</p>}
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
        {description && <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
