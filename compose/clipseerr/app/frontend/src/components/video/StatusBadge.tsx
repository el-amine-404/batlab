import { Ban, Check, Clock3, Download, Loader2, Send, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { RequestStatus } from "@/lib/api";

const STATUS: Record<RequestStatus, { label: string; variant: "default" | "success" | "warning" | "destructive" | "muted" | "accent"; icon: typeof Check }> = {
  queued: { label: "Waiting", variant: "muted", icon: Clock3 },
  sent: { label: "With a service", variant: "accent", icon: Send },
  downloading: { label: "Downloading", variant: "accent", icon: Download },
  completed: { label: "Done", variant: "success", icon: Check },
  failed: { label: "Failed", variant: "destructive", icon: X },
  cancelled: { label: "Cancelled", variant: "muted", icon: Ban },
};

export function StatusBadge({ status, service }: { status: RequestStatus; service?: string }) {
  const { label, variant, icon: Icon } = STATUS[status];
  const text = status === "sent" && service ? `With ${service}` : status === "downloading" && service ? `${service} downloading` : label;
  return (
    <Badge variant={variant}>
      {status === "downloading" ? <Loader2 className="animate-spin" /> : <Icon />}
      {text}
    </Badge>
  );
}
