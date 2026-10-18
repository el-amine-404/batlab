import { NumberField, Section, SettingsScreen } from "@/components/settings/fields";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDestinations } from "@/lib/queries";

const NONE = "__none";

export default function GeneralPage() {
  const { data: destinations } = useDestinations();
  return (
    <SettingsScreen title="General" description="Where downloads go when nobody says, the page's address, and how long downloads are remembered.">
      {(draft) => (
        <>
          <Section title="Where downloads go" description="For a download that names no destination: an app that doesn't say, or a link pasted here.">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="default_destination">Default destination</Label>
              <Select value={draft.value("default_destination") || NONE} onValueChange={(value) => draft.set("default_destination", value === NONE ? "" : value)}>
                <SelectTrigger id="default_destination" className="max-w-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>Nowhere: kept here for whoever asked</SelectItem>
                  {(destinations?.destinations ?? []).map((d) => <SelectItem key={d.slug} value={d.slug}>{d.name} · media/{d.folder}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">"Nowhere" keeps the file in videodrop for a while (Storage), to download from this page or by the app that asked.</p>
            </div>
          </Section>
          <Section title="History">
            <NumberField draft={draft} name="keep_days" label="Remember ended downloads for" unit="days" min={0} max={3650}
                         help="Then they leave the Downloads list; their files stay where they are. 0: forever." />
          </Section>
          <Section title="This page">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="public_url">Its address</Label>
              <Input id="public_url" type="url" value={draft.value("public_url") ?? ""} placeholder="http://videodrop.homelab.lan" className="max-w-md"
                     onChange={(e) => draft.set("public_url", e.target.value.trim())} />
              <p className="text-xs text-muted-foreground">Messages (Discord…) link to it. Empty: they carry no link.</p>
            </div>
          </Section>
        </>
      )}
    </SettingsScreen>
  );
}
