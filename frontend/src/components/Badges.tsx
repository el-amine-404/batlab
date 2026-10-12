import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Status } from "@/lib/api"
import { FORMAT, STATUS } from "@/lib/format"
import { cn } from "@/lib/utils"

export function StatusBadge({ status, className }: { status: Status; className?: string }) {
  const s = STATUS[status]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap", s.tone, className)}>
          <span className={cn("size-1.5 rounded-full", s.dot)} />{s.label}
        </span>
      </TooltipTrigger>
      <TooltipContent>{s.hint}</TooltipContent>
    </Tooltip>
  )
}

export function FormatBadge({ format, synced }: { format: string; synced?: boolean }) {
  return (
    <span className={cn("inline-flex h-5 items-center rounded border px-1.5 text-[10px] font-medium tracking-wide uppercase",
      synced ? "border-sky-500/30 bg-sky-500/10 text-sky-300" : "border-border bg-muted/40 text-muted-foreground")}>
      {FORMAT[format] ?? format}
    </span>
  )
}

export function LanguageBadge({ name }: { name: string }) {
  return <span className="inline-flex h-5 items-center rounded bg-muted px-1.5 text-[11px] text-muted-foreground">{name}</span>
}
