import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";

import { cn } from "@/lib/utils";

/**
 * The search field above every screen. Typing goes to /search?q=… (replacing
 * the history entry while typing, so Back leaves search); "/" focuses it.
 */
export function SearchBox({ className }: { className?: string }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const onSearch = pathname === "/search";
  const [text, setText] = useState(onSearch ? (params.get("q") ?? "") : "");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (onSearch) setText(params.get("q") ?? "");
    else setText("");
  }, [onSearch, params]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const typing = target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if ((event.key === "/" && !typing) || (event.key === "k" && (event.metaKey || event.ctrlKey))) {
        event.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Typing pauses: the results follow.
  useEffect(() => {
    const q = text.trim();
    if (!onSearch && !q) return;
    const handle = window.setTimeout(() => {
      if (onSearch && (params.get("q") ?? "") === q) return;
      navigate(q ? `/search?q=${encodeURIComponent(q)}` : "/search", { replace: onSearch });
    }, 250);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  return (
    <form
      role="search"
      className={cn("relative w-full", className)}
      onSubmit={(event) => {
        event.preventDefault();
        const q = text.trim();
        if (q) navigate(`/search?q=${encodeURIComponent(q)}`, { replace: onSearch });
      }}
    >
      <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        ref={input}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Search videos and artists"
        aria-label="Search videos and artists"
        enterKeyHint="search"
        autoComplete="off"
        spellCheck={false}
        className="h-11 w-full rounded-xl border border-input bg-black/25 pr-20 pl-10 text-sm shadow-xs transition-[border-color,box-shadow] outline-none placeholder:text-muted-foreground/80 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/30"
      />
      {text ? (
        <button
          type="button"
          onClick={() => {
            setText("");
            input.current?.focus();
          }}
          aria-label="Clear the search"
          className="absolute top-1/2 right-2 flex size-8 -translate-y-1/2 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      ) : (
        <kbd className="pointer-events-none absolute top-1/2 right-3 hidden -translate-y-1/2 rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground sm:block">
          /
        </kbd>
      )}
    </form>
  );
}
