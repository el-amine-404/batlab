import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/PageHeader";
import { Failed, Loading } from "@/components/layout/States";
import { PROFILES } from "@/components/scan/labels";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText, type Profile, type Settings } from "@/lib/api";

type Values = Settings["values"];
const MB = 1048576;
const FIELDS: { key: keyof Values; label: string; hint: string; factor?: number }[] = [
  { key: "retention_days", label: "Delete scans after (days)", hint: "0 keeps them until someone deletes them. Applies to new scans." },
  { key: "max_files_per_project", label: "Pages in a scan", hint: "At most 1000." },
  { key: "max_upload_bytes", label: "Largest photo (MB)", hint: "At most 100.", factor: MB },
  { key: "max_pixels", label: "Largest photo (megapixels)", hint: "A guard against decompression bombs.", factor: 1_000_000 },
  { key: "jpeg_quality", label: "JPEG quality", hint: "70 to 100." },
  { key: "export_ttl_minutes", label: "Keep PDFs and ZIPs for (minutes)", hint: "They can be made again at any time." },
  { key: "worker_max_attempts", label: "Tries for a page", hint: "Before it is marked failed." },
];

export default function GeneralPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<Values | null>(null);
  useEffect(() => { if (query.data && !draft) setDraft(query.data.values); }, [query.data, draft]);
  const save = useMutation({
    mutationFn: () => api.saveSettings(draft!, query.data!.revision),
    onSuccess: (saved) => { client.setQueryData(["settings"], saved); setDraft(saved.values); toast.success("Saved"); },
    onError: (error) => toast.error(errorText(error)),
  });
  if (query.error) return <Failed message={errorText(query.error)} />;
  if (!draft) return <Loading />;
  return (
    <>
      <PageHeader title="General" description="Limits and defaults. The same values are in the API." />
      <Card className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1.5">
          <Label id="finish">Finish of a new scan</Label>
          <Select value={draft.default_profile} onValueChange={(value) => setDraft({ ...draft, default_profile: value as Profile })}>
            <SelectTrigger aria-labelledby="finish" className="max-w-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{PROFILES.map(({ id, label }) => <SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map(({ key, label, hint, factor = 1 }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={key}>{label}</Label>
              <Input id={key} type="number" inputMode="numeric" value={Number(draft[key]) / factor} onChange={(event) => setDraft({ ...draft, [key]: Math.round(Number(event.target.value) * factor) })} />
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDraft(query.data!.values)}>Reset</Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </div>
      </Card>
    </>
  );
}
