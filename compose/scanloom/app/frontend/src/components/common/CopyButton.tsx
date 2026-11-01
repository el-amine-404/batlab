import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { toClipboard } from "@/lib/clipboard";

/** Copies text (made when clicked, when it's a function); a tick for a moment says it did. */
export function CopyButton({ text, label }: { text: string | (() => string); label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await toClipboard(typeof text === "function" ? text() : text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(`Couldn't copy: select the ${label} and copy it by hand.`);
    }
  };
  return (
    <Button type="button" variant="ghost" size="icon-sm" onClick={() => void copy()} aria-label={`Copy the ${label}`} title={`Copy the ${label}`}>
      {copied ? <Check className="text-success" /> : <Copy />}
    </Button>
  );
}
