import { CloudOff, Loader2, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";

export function Loading() {
  return (
    <div className="flex justify-center py-16 text-muted-foreground" role="status" aria-label="Loading">
      <Loader2 className="animate-spin" />
    </div>
  );
}

export function Failed({ message }: { message: string }) {
  return (
    <Card className="flex items-center gap-3 border-destructive/30 p-4 text-sm text-destructive">
      <CloudOff className="size-4 shrink-0" /> {message}
    </Card>
  );
}

export function Empty({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <Card className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Icon className="size-8 text-muted-foreground/60" />
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-sm text-xs text-muted-foreground">{children}</div>}
    </Card>
  );
}
