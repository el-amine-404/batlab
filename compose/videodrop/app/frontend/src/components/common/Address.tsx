import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";

import { CopyButton } from "./CopyButton";

export function Address({ label, hint, url }: { label: string; hint: string; url: string }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{label} <span className="font-normal text-muted-foreground">· {hint}</span></p>
        <code className="scroll-thin block overflow-x-auto text-xs whitespace-nowrap text-muted-foreground">{url}</code>
      </div>
      <CopyButton text={url} label={`${label} address`} />
      <Button variant="ghost" size="icon-sm" asChild>
        <a href={url} target="_blank" rel="noreferrer" aria-label={`Open the ${label} address`}><ExternalLink /></a>
      </Button>
    </div>
  );
}
