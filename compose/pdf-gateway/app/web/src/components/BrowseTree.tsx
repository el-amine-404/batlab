import { useState } from "react";
import { ChevronRight, Folder, FolderOpen, Layers } from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router";
import { tree, useLibrary } from "../library";
import { cx } from "./ui";

/** The archive as its folders are: sections, then correspondents, with counts. */
export function BrowseTree({ onPick }: { onPick?: () => void }) {
  const library = useLibrary();
  const [params] = useSearchParams();
  const location = useLocation();
  const onDocuments = location.pathname === "/documents";
  const activeSection = onDocuments ? params.get("section") : null;
  const activeFrom = onDocuments ? params.get("from") : null;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!library.data) return <p className="px-3 text-xs text-muted-foreground">{library.error ? "Couldn't load the archive." : "Loading…"}</p>;
  const sections = tree(library.data);
  if (!sections.length) return <p className="px-3 text-xs text-muted-foreground">Nothing saved yet.</p>;
  const link = "flex h-9 items-center justify-between gap-2 rounded-lg px-3 text-sm transition";
  const idle = "text-muted-foreground hover:bg-accent hover:text-foreground";
  const current = "bg-accent font-medium text-foreground";
  return (
    <ul className="space-y-0.5" data-testid="browse-tree">
      <li>
        <Link to="/documents" onClick={onPick} className={cx(link, onDocuments && !activeSection && !params.get("show") ? current : idle)}>
          <span className="flex items-center gap-2"><Layers className="size-4" /> Everything</span><span className="text-xs tabular-nums">{library.data.length}</span>
        </Link>
      </li>
      {sections.map((item) => {
        const expanded = open[item.section] ?? activeSection === item.section;
        const Icon = expanded ? FolderOpen : Folder;
        return (
          <li key={item.section} data-section={item.section}>
            <div className="flex items-center">
              <button type="button" aria-label={`${expanded ? "Hide" : "Show"} correspondents in ${item.section}`} aria-expanded={expanded}
                disabled={!item.correspondents.length}
                className="flex size-7 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent disabled:opacity-0"
                onClick={() => setOpen((state) => ({ ...state, [item.section]: !expanded }))}>
                <ChevronRight className={cx("size-3.5 transition", expanded && "rotate-90")} />
              </button>
              <Link to={`/documents?section=${encodeURIComponent(item.section)}`} onClick={onPick}
                className={cx(link, "min-w-0 flex-1 pl-1.5", activeSection === item.section && !activeFrom ? current : idle)}>
                <span className="flex min-w-0 items-center gap-2"><Icon className="size-4 shrink-0" /><span className="truncate">{item.section}</span></span>
                <span className="text-xs tabular-nums">{item.count}</span>
              </Link>
            </div>
            {expanded && (
              <ul className="mt-0.5 ml-9 space-y-0.5 border-l border-border pl-2">
                {item.correspondents.map((name) => (
                  <li key={name.name}>
                    <Link to={`/documents?section=${encodeURIComponent(item.section)}&from=${encodeURIComponent(name.name)}`} onClick={onPick}
                      className={cx(link, "h-8 text-xs", activeSection === item.section && activeFrom === name.name ? current : idle)}>
                      <span className="truncate">{name.name}</span><span className="tabular-nums">{name.count}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
