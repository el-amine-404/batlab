import { useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
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
  if (!library.data) return <p className="px-3 text-xs text-slate-500">{library.error ? "Couldn't load the archive." : "Loading…"}</p>;
  const sections = tree(library.data);
  if (!sections.length) return <p className="px-3 text-xs text-slate-500 dark:text-slate-400">Nothing saved yet.</p>;
  const link = "flex min-h-9 items-center justify-between gap-2 rounded-lg px-3 text-sm";
  return (
    <ul className="space-y-0.5" data-testid="browse-tree">
      <li>
        <Link to="/documents" onClick={onPick} className={cx(link, onDocuments && !activeSection && !params.get("show")
          ? "bg-slate-100 font-semibold dark:bg-slate-800" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800")}>
          <span>Everything</span><span className="text-xs text-slate-500">{library.data.length}</span>
        </Link>
      </li>
      {sections.map((item) => {
        const expanded = open[item.section] ?? activeSection === item.section;
        return (
          <li key={item.section} data-section={item.section}>
            <div className="flex items-center">
              <button type="button" aria-label={`${expanded ? "Hide" : "Show"} correspondents in ${item.section}`} aria-expanded={expanded}
                disabled={!item.correspondents.length}
                className="flex size-7 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-100 disabled:opacity-0 dark:hover:bg-slate-800"
                onClick={() => setOpen((current) => ({ ...current, [item.section]: !expanded }))}>
                <span className={cx("inline-block transition", expanded && "rotate-90")}>›</span>
              </button>
              <Link to={`/documents?section=${encodeURIComponent(item.section)}`} onClick={onPick}
                className={cx(link, "min-w-0 flex-1 pl-1", activeSection === item.section && !activeFrom
                  ? "bg-slate-100 font-semibold dark:bg-slate-800" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800")}>
                <span className="truncate">{item.section}</span><span className="text-xs text-slate-500">{item.count}</span>
              </Link>
            </div>
            {expanded && (
              <ul className="ml-7 space-y-0.5">
                {item.correspondents.map((name) => (
                  <li key={name.name}>
                    <Link to={`/documents?section=${encodeURIComponent(item.section)}&from=${encodeURIComponent(name.name)}`} onClick={onPick}
                      className={cx(link, "min-h-8 text-xs", activeSection === item.section && activeFrom === name.name
                        ? "bg-slate-100 font-semibold dark:bg-slate-800" : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800")}>
                      <span className="truncate">{name.name}</span><span className="text-slate-500">{name.count}</span>
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
