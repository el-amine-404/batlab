import { DropdownMenu as Primitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const Menu = Primitive.Root;
export const MenuTrigger = Primitive.Trigger;

export function MenuContent({ className, align = "end", ...props }: ComponentProps<typeof Primitive.Content>) {
  return (
    <Primitive.Portal>
      <Primitive.Content align={align} sideOffset={6} collisionPadding={8}
                         className={cn("z-[60] min-w-44 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-2xl", className)} {...props} />
    </Primitive.Portal>
  );
}

export function MenuItem({ className, danger, ...props }: ComponentProps<typeof Primitive.Item> & { danger?: boolean }) {
  return (
    <Primitive.Item
      className={cn("flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2.5 text-sm outline-none select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-accent [&_svg]:size-4 [&_svg]:shrink-0",
                    danger ? "text-destructive" : "text-foreground", className)}
      {...props} />
  );
}

export function MenuLink({ href, download, children }: { href: string; download?: boolean; children: React.ReactNode }) {
  return <MenuItem asChild><a href={href} download={download}>{children}</a></MenuItem>;
}
