import * as React from "react";

import { cn } from "@/lib/utils";

function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card"
      className={cn(
        "rounded-xl border border-border bg-card text-card-foreground shadow-xl shadow-black/20 backdrop-blur-xl",
        className,
      )}
      {...props}
    />
  );
}

export { Card };
