import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { toClipboard } from "@/lib/clipboard";

export function Address({ label, hint, url }: { label: string; hint: string; url: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await toClipboard(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy: select the address and copy it by hand.");
    }
  };
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{label} <span className="font-normal text-muted-foreground">· {hint}</span></p>
        <code className="block truncate text-xs text-muted-foreground" title={url}>{url}</code>
      </div>
      <Button variant="ghost" size="icon-sm" onClick={() => void copy()} aria-label={`Copy the ${label} address`}>
        {copied ? <Check className="text-success" /> : <Copy />}
      </Button>
      <Button variant="ghost" size="icon-sm" asChild>
        <a href={url} target="_blank" rel="noreferrer" aria-label={`Open the ${label} address`}><ExternalLink /></a>
      </Button>
    </div>
  );
}
