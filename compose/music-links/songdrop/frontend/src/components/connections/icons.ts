import { BellRing, MessageCircle, Plug, Webhook, type LucideIcon } from "lucide-react";

const ICONS: Record<string, LucideIcon> = { apprise: BellRing, discord: MessageCircle, webhook: Webhook };

/** A type's icon; a type the page doesn't know yet still shows, with a plug. */
export const iconFor = (type: string): LucideIcon => ICONS[type] ?? Plug;
