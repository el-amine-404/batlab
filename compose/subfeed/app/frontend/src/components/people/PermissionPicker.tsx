import { Checkbox } from "@/components/ui/checkbox";
import type { Permission } from "@/lib/api";
import { usePermissionInfo } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** Common sets, to start from. */
export const PRESETS: { label: string; permissions: Permission[] }[] = [
  { label: "Admin", permissions: ["admin"] },
  { label: "Editor", permissions: ["feeds"] },
  { label: "Reader", permissions: [] },
];

/** What someone (or an app's key) may do: a box for each permission; Admin holds them all. */
export function PermissionPicker({ value, onChange, allowAdmin = true, idPrefix }: {
  value: Permission[];
  onChange: (next: Permission[]) => void;
  allowAdmin?: boolean;
  idPrefix: string;
}) {
  const info = usePermissionInfo().filter((item) => allowAdmin || item.id !== "admin");
  const admin = value.includes("admin");
  const toggle = (id: Permission, on: boolean) => onChange(on ? [...value.filter((p) => p !== id), id] : value.filter((p) => p !== id));
  return (
    <div className="flex flex-col gap-1">
      {allowAdmin && (
        <div className="mb-1 flex flex-wrap gap-1.5">
          {PRESETS.map((preset) => {
            const same = preset.permissions.length === value.length && preset.permissions.every((p) => value.includes(p));
            return (
              <button key={preset.label} type="button" onClick={() => onChange(preset.permissions)}
                      className={cn("rounded-full border px-2.5 py-0.5 text-xs transition-colors",
                                    same ? "border-orange-400/50 bg-orange-400/15 text-foreground" : "border-border text-muted-foreground hover:text-foreground")}>
                {preset.label}
              </button>
            );
          })}
        </div>
      )}
      {info.map((item) => {
        const implied = admin && item.id !== "admin";
        return (
          <label key={item.id} htmlFor={`${idPrefix}-${item.id}`}
                 className={cn("flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-white/[0.03]", implied && "cursor-default opacity-60")}>
            <Checkbox id={`${idPrefix}-${item.id}`} className="mt-0.5" checked={admin || value.includes(item.id)} disabled={implied}
                      onCheckedChange={(on) => toggle(item.id, on === true)} />
            <span className="min-w-0">
              <span className="block text-sm font-medium">{item.label}</span>
              <span className="block text-xs text-muted-foreground">{item.description}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}

/** "Manage feeds, Change settings" for a list. */
export function usePermissionLabels(): (permissions: Permission[]) => string {
  const info = usePermissionInfo();
  return (permissions) => {
    if (permissions.includes("admin")) return "Admin: everything";
    return permissions.map((id) => info.find((item) => item.id === id)?.label ?? id).join(", ") || "Reading only";
  };
}
