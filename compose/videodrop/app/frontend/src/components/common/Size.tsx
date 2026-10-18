import { Tooltip } from "@/components/ui/tooltip";
import { cn, formatBytes } from "@/lib/utils";

/**
 * A size as a person reads it ("1006 MB"); on hover, exactly: in bytes, and
 * in the two ways sizes are counted (disks are sold in 1000s, systems count
 * in 1024s).
 */
export function Size({ bytes, approximate = false, how, className }: {
  bytes: number;
  approximate?: boolean;
  /** Where the number comes from, when it isn't the file's own. */
  how?: string;
  className?: string;
}) {
  const decimal = bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e3).toFixed(1)} kB`;
  const binary = bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${(bytes / 1024).toFixed(1)} KiB`;
  return (
    <Tooltip content={
      <div className="flex flex-col gap-0.5 tabular-nums">
        <span className="font-medium">{approximate && "about "}{bytes.toLocaleString()} bytes</span>
        <span className="text-muted-foreground">{decimal} · {binary}</span>
        {how && <span className="mt-1 text-muted-foreground">{how}</span>}
      </div>
    }>
      <span tabIndex={0} className={cn("cursor-help underline decoration-muted-foreground/40 decoration-dotted underline-offset-4", className)}>
        {approximate && "≈ "}{formatBytes(bytes)}
      </span>
    </Tooltip>
  );
}
