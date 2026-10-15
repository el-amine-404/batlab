import { AlertCircle, ChevronRight, CircleCheck, CornerLeftUp, Folder, FolderOpen, Info, Loader2 } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, errorText, type FolderCheck, type FolderListing } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";

const parentOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
const lastPart = (path: string) => path.slice(path.lastIndexOf("/") + 1);

/** The folders inside `path`, loaded when `path` changes; null while loading. */
function useFolders(path: string | null) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    setListing(null);
    setError(null);
    api.folders(path, controller.signal).then(setListing, (caught) => {
      if (!controller.signal.aborted) setError(errorText(caught, "Couldn't list the folders."));
    });
    return () => controller.abort();
  }, [path]);
  return { listing, error };
}

/** Whether songs can go to `path`, asked once typing pauses. */
function useFolderCheck(path: string, onValidity: (ok: boolean) => void) {
  const [check, setCheck] = useState<FolderCheck | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setCheck(null);
    const handle = window.setTimeout(() => {
      api.checkFolder(path, controller.signal).then(
        (result) => {
          setCheck(result);
          onValidity(result.ok);
        },
        (caught) => {
          if (controller.signal.aborted) return;
          setCheck({ path, ok: false, exists: false, message: errorText(caught), free_bytes: null });
          onValidity(false);
        },
      );
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(handle);
    };
    // onValidity is the screen's setter; a new one each render must not re-ask.
  }, [path]);
  return check;
}

/**
 * A folder under the output root: typed with suggestions from the folders
 * that exist, or chosen in a folder browser; checked as it changes. The
 * browser lists the server's folders (the browser's own folder picker would
 * show this computer's).
 */
export function FolderPicker({
  id,
  root,
  value,
  onChange,
  onValidity,
  disabled = false,
}: {
  id: string;
  root: string;
  value: string;
  onChange: (value: string) => void;
  onValidity: (ok: boolean) => void;
  disabled?: boolean;
}) {
  const listId = useId();
  const [focused, setFocused] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [browsing, setBrowsing] = useState(false);
  const check = useFolderCheck(value, onValidity);
  const { listing } = useFolders(focused ? parentOf(value) : null);

  const typed = lastPart(value).toLowerCase();
  const suggestions = (listing?.folders ?? [])
    .filter((folder) => folder.name.toLowerCase().startsWith(typed) && folder.path !== value)
    .slice(0, 8);
  const showList = focused && suggestions.length > 0;

  const choose = (path: string) => {
    onChange(path);
    setHighlight(0);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!showList) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setHighlight((current) => (current + step + suggestions.length) % suggestions.length);
    } else if (event.key === "Enter" || event.key === "Tab") {
      if (event.key === "Enter") event.preventDefault();
      if (suggestions[highlight]) choose(suggestions[highlight].path);
    } else if (event.key === "Escape") {
      setFocused(false);
    }
  };

  return (
    <div className={cn(disabled && "pointer-events-none opacity-50")}>
      <div className="relative">
        <div className="flex items-center rounded-lg border border-input bg-black/20 focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/40">
          <button
            type="button"
            onClick={() => setBrowsing(true)}
            disabled={disabled}
            title="Browse the folders"
            aria-label="Browse the folders"
            className="flex h-10 items-center gap-2 rounded-l-lg border-r border-input px-3 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <FolderOpen className="size-4" />
            <span className="font-mono text-xs">{root}/</span>
          </button>
          <input
            id={id}
            value={value}
            disabled={disabled}
            onChange={(event) => {
              onChange(event.target.value);
              setHighlight(0);
            }}
            onFocus={() => setFocused(true)}
            onBlur={() => window.setTimeout(() => setFocused(false), 150)}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded={showList}
            aria-controls={listId}
            aria-autocomplete="list"
            spellCheck={false}
            autoComplete="off"
            className="h-10 min-w-0 flex-1 bg-transparent px-3 font-mono text-sm outline-none"
          />
        </div>
        {showList && (
          <ul
            id={listId}
            role="listbox"
            className="absolute inset-x-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-2xl"
          >
            {suggestions.map((folder, position) => (
              <li
                key={folder.path}
                role="option"
                aria-selected={position === highlight}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(folder.path);
                }}
                onMouseEnter={() => setHighlight(position)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 font-mono text-sm",
                  position === highlight && "bg-accent",
                )}
              >
                <Folder className="size-3.5 text-muted-foreground" /> {folder.path}
              </li>
            ))}
          </ul>
        )}
      </div>
      <CheckLine check={check} />
      <FolderBrowser
        open={browsing}
        root={root}
        start={check?.exists ? value : parentOf(value)}
        onClose={() => setBrowsing(false)}
        onChoose={(path) => {
          choose(path);
          setBrowsing(false);
        }}
      />
    </div>
  );
}

function CheckLine({ check }: { check: FolderCheck | null }) {
  if (!check) {
    return (
      <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Checking the folder…
      </p>
    );
  }
  const Icon = !check.ok ? AlertCircle : check.exists ? CircleCheck : Info;
  return (
    <p className={cn("mt-2 flex items-center gap-1.5 text-xs", check.ok ? (check.exists ? "text-success" : "text-muted-foreground") : "text-destructive")}>
      <Icon className="size-3.5 shrink-0" />
      {check.message}
      {check.ok && check.free_bytes !== null && <span className="text-muted-foreground">· {formatBytes(check.free_bytes)} free</span>}
    </p>
  );
}

function FolderBrowser({
  open,
  root,
  start,
  onClose,
  onChoose,
}: {
  open: boolean;
  root: string;
  start: string;
  onClose: () => void;
  onChoose: (path: string) => void;
}) {
  const [path, setPath] = useState(start);
  useEffect(() => {
    if (open) setPath(start);
  }, [open, start]);
  const { listing, error } = useFolders(open ? path : null);
  const parts = path ? path.split("/") : [];

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose the output folder</DialogTitle>
          <DialogDescription>Folders inside {root}. A new folder can be typed in the field; songdrop makes it.</DialogDescription>
        </DialogHeader>
        <nav aria-label="Folder" className="flex flex-wrap items-center gap-1 border-b border-border px-5 py-2 font-mono text-xs">
          <button type="button" onClick={() => setPath("")} className="rounded px-1.5 py-1 hover:bg-accent">
            {root}
          </button>
          {parts.map((part, position) => (
            <span key={position} className="flex items-center gap-1">
              <ChevronRight className="size-3 text-muted-foreground" />
              <button type="button" onClick={() => setPath(parts.slice(0, position + 1).join("/"))} className="rounded px-1.5 py-1 hover:bg-accent">
                {part}
              </button>
            </span>
          ))}
        </nav>
        <DialogBody className="min-h-64 p-2">
          {error && <p className="p-3 text-sm text-destructive">{error}</p>}
          {!listing && !error && (
            <div className="flex justify-center py-10 text-muted-foreground">
              <Loader2 className="animate-spin" />
            </div>
          )}
          {listing && (
            <ul className="flex flex-col">
              {listing.parent !== null && (
                <li>
                  <button type="button" onClick={() => setPath(listing.parent ?? "")} className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent">
                    <CornerLeftUp className="size-4" /> Up
                  </button>
                </li>
              )}
              {listing.folders.map((folder) => (
                <li key={folder.path}>
                  <button type="button" onClick={() => setPath(folder.path)} className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm hover:bg-accent">
                    <Folder className="size-4 text-violet-300" /> <span className="flex-1 truncate">{folder.name}</span>
                    <ChevronRight className="size-4 text-muted-foreground" />
                  </button>
                </li>
              ))}
              {listing.folders.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No folders in here.</li>}
            </ul>
          )}
        </DialogBody>
        <DialogFooter className="justify-end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!path} onClick={() => onChoose(path)}>
            Use {path ? lastPart(path) : "this folder"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
