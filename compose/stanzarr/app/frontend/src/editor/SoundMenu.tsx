import { AudioLines, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

const HEAR = [
  { value: "mix", label: "Song", hint: "The full mix, as it is released" },
  { value: "voice", label: "Voice", hint: "The voice alone: easier to hear where words start" },
]
const SHOW = [
  { value: "mix", label: "Song", dot: "bg-zinc-500" },
  { value: "voice", label: "Voice", dot: "bg-teal-400" },
]

/** What you hear and what the waveform draws, kept out of the way. */
export function SoundMenu({ source, onSource, shown, onShown, hasVoice }: {
  source: string; onSource: (v: "mix" | "voice") => void
  shown: string[]; onShown: (v: string[]) => void; hasVoice: boolean
}) {
  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="sm" tabIndex={-1} aria-label="Sound and waveform"
              className="gap-2 font-normal text-muted-foreground hover:text-foreground">
              <AudioLines className="size-4" />
              Hearing {source === "voice" ? "voice" : "song"}
              <ChevronDown className="size-3.5 opacity-60" />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">Sound and waveform <span className="opacity-60">(V switches the sound)</span></TooltipContent>
      </Tooltip>
      <PopoverContent align="end" className="w-80 space-y-5 p-4">
        <section className="space-y-3">
          <h3 className="text-xs font-medium text-muted-foreground">You hear</h3>
          <RadioGroup value={source} onValueChange={(v) => onSource(v as "mix" | "voice")} className="gap-3">
            {HEAR.map((o) => (
              <Label key={o.value} className="flex cursor-pointer items-start gap-3 font-normal has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50">
                <RadioGroupItem value={o.value} className="mt-0.5" disabled={o.value === "voice" && !hasVoice} />
                <span className="space-y-0.5">
                  <span className="block font-medium">{o.label}</span>
                  <span className="block text-xs text-muted-foreground">{o.hint}</span>
                </span>
              </Label>
            ))}
          </RadioGroup>
        </section>
        <section className="space-y-3">
          <h3 className="text-xs font-medium text-muted-foreground">The waveform shows</h3>
          {SHOW.map((o) => (
            <Label key={o.value} className="flex cursor-pointer items-center gap-3 font-normal has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50">
              <Checkbox checked={shown.includes(o.value)} disabled={o.value === "voice" && !hasVoice}
                onCheckedChange={(on) => onShown(on ? [...shown, o.value] : shown.filter((v) => v !== o.value))} />
              <span className={`size-2 rounded-full ${o.dot}`} />
              {o.label}
            </Label>
          ))}
        </section>
        {!hasVoice && (
          <p className="text-xs text-muted-foreground">The voice alone comes with an AI sync: the worker separates it from the music.</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
