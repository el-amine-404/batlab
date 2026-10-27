import { Dialog as DialogPrimitive, Select as SelectPrimitive } from "radix-ui";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { X } from "lucide-react";
import { cx } from "./lib";

export function Button({ className, variant = "primary", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  return <button className={cx("button", `button-${variant}`, className)} {...props} />;
}
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) { return <div className={cx("card", className)} {...props} />; }
export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" }) { return <span className={`badge badge-${tone}`}>{children}</span>; }
export function Select({ value, onValueChange, options, label }: { value: string; onValueChange: (value: string) => void; options: Array<[string, string]>; label: string }) {
  return <SelectPrimitive.Root value={value} onValueChange={onValueChange}>
    <SelectPrimitive.Trigger className="select" aria-label={label}><SelectPrimitive.Value /></SelectPrimitive.Trigger>
    <SelectPrimitive.Portal><SelectPrimitive.Content className="select-menu" position="popper"><SelectPrimitive.Viewport>
      {options.map(([key, name]) => <SelectPrimitive.Item key={key} value={key} className="select-item"><SelectPrimitive.ItemText>{name}</SelectPrimitive.ItemText></SelectPrimitive.Item>)}
    </SelectPrimitive.Viewport></SelectPrimitive.Content></SelectPrimitive.Portal>
  </SelectPrimitive.Root>;
}
export function Dialog({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode }) {
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}><DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="dialog-overlay" />
    <DialogPrimitive.Content className="dialog-content"><div className="dialog-head"><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Close className="icon-button"><X /></DialogPrimitive.Close></div>{children}</DialogPrimitive.Content>
  </DialogPrimitive.Portal></DialogPrimitive.Root>;
}
