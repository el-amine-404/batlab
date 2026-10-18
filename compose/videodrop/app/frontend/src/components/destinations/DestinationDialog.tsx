import { Loader2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { OptionsFields } from "@/components/jobs/OptionsFields";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type Destination, errorText, type OptionsPatch } from "@/lib/api";
import { useSettings, useTemplateFields } from "@/lib/queries";

import { FolderPicker } from "./FolderPicker";

/** Makes a destination, or changes one (saved). */
export function DestinationDialog({ saved, open, onOpenChange, onSaved }: {
  saved?: Destination;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const { data: settings } = useSettings();
  const { data: template } = useTemplateFields();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [folder, setFolder] = useState("");
  const [folderOk, setFolderOk] = useState(false);
  const [pattern, setPattern] = useState("");
  const [options, setOptions] = useState<OptionsPatch>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(saved?.name ?? "");
    setSlug(saved?.slug ?? "");
    setFolder(saved?.folder ?? "");
    setPattern(saved?.template ?? template?.default ?? "{artist}/{title} [{id}]");
    setOptions(saved?.options ?? {});
  }, [open, saved, template]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const body = { name: name.trim(), folder, template: pattern, options };
      if (saved) await api.updateDestination(saved.slug, body);
      else await api.createDestination({ ...body, slug: slug.trim() || undefined });
      toast.success(saved ? "Saved" : `${body.name} added`);
      onSaved();
      onOpenChange(false);
    } catch (caught) {
      toast.error(errorText(caught, "Couldn't save it."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <form onSubmit={save}>
          <DialogHeader>
            <DialogTitle>{saved ? `Change ${saved.name}` : "New destination"}</DialogTitle>
            <DialogDescription>
              A folder downloads can be sent to by name. Changes apply to the downloads asked for after; files already there stay.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="dest-name">Name</Label>
                <Input id="dest-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} placeholder="Music videos" autoFocus={!saved} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="dest-slug">Name in the API</Label>
                <Input id="dest-slug" value={slug} onChange={(e) => setSlug(e.target.value)} disabled={!!saved} maxLength={40} pattern="[a-z0-9\-]+"
                       placeholder={name ? name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "music-videos"} spellCheck={false} />
                <p className="text-xs text-muted-foreground">{saved ? "Fixed: apps use it." : "What apps send as destination. Fixed once made."}</p>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dest-folder">Folder</Label>
              <FolderPicker id="dest-folder" root="media" value={folder} onChange={setFolder} onValidity={setFolderOk} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dest-template">File name</Label>
              <Input id="dest-template" value={pattern} onChange={(e) => setPattern(e.target.value)} required maxLength={200} className="font-mono" spellCheck={false} />
              <p className="text-xs text-muted-foreground">
                A path inside the folder, without the extension; / makes folders. Must hold {"{id}"}. Fields:{" "}
                {(template?.fields ?? []).map((field) => (
                  <button key={field} type="button" onClick={() => setPattern(`${pattern}{${field}}`)}
                          className="mr-1 rounded bg-secondary px-1 font-mono text-[11px] hover:bg-accent">{`{${field}}`}</button>
                ))}
              </p>
            </div>
            {settings && (
              <div className="flex flex-col gap-2">
                <p className="text-sm font-medium">Options for downloads to it</p>
                <OptionsFields id="dest" value={options} inherited={settings.defaults} onChange={setOptions} key={saved?.slug ?? "new"} />
              </div>
            )}
          </DialogBody>
          <DialogFooter className="justify-end">
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !name.trim() || !folderOk}>
              {busy && <Loader2 className="animate-spin" />} {saved ? "Save" : "Add it"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
