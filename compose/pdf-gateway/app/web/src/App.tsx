import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { api, ApiError, setCsrf, type Session } from "./api";
import { Button, cx } from "./components/ui";
import { LoginPage } from "./pages/Login";
import { UploadPage } from "./pages/Upload";
import { DocumentsPage } from "./pages/Documents";
import { DocumentPage } from "./pages/Document";
import { SystemPage } from "./pages/System";

const NAV = [
  { to: "/", label: "Add files", end: true },
  { to: "/documents", label: "Documents" },
  { to: "/system", label: "System" },
];

export function App() {
  const client = useQueryClient();
  const session = useQuery<Session | null>({
    queryKey: ["session"],
    queryFn: async () => {
      try {
        const value = await api.session();
        setCsrf(value.csrfToken);
        return value;
      } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 400)) { setCsrf(null); return null; }
        throw error;
      }
    },
    staleTime: 60_000,
  });

  if (session.isPending) return <p className="p-6 text-sm text-slate-500">Loading…</p>;
  if (!session.data) return <LoginPage onLogin={(value) => { setCsrf(value.csrfToken); client.setQueryData(["session"], value); }} />;

  const logout = async () => {
    try { await api.logout(); } finally {
      setCsrf(null);
      // Sign out first (the shell observes this query), then drop every other cached response.
      client.setQueryData(["session"], null);
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "session" });
    }
  };

  return (
    <div className="min-h-dvh pb-20 sm:pb-0">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-2">
          <span className="font-semibold">PDF Gateway</span>
          <nav className="hidden gap-1 sm:flex" aria-label="Main">
            {NAV.map((item) => <NavItem key={item.to} {...item} />)}
          </nav>
          <div className="flex items-center gap-1 text-sm text-slate-600 dark:text-slate-400">
            <ThemeSwitch />
            <span className="hidden px-2 sm:inline" data-testid="whoami">{session.data.username}</span>
            <Button variant="ghost" onClick={logout}>Sign out</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-4">
        <Routes>
          <Route path="/" element={<UploadPage session={session.data} />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:scanId" element={<DocumentPage session={session.data} />} />
          <Route path="/system" element={<SystemPage />} />
          {/* Addresses of the first version of this UI. */}
          <Route path="/upload" element={<Navigate to="/" replace />} />
          <Route path="/jobs" element={<Navigate to="/documents" replace />} />
          <Route path="/archive" element={<Navigate to="/documents" replace />} />
          <Route path="/quarantine" element={<Navigate to="/documents?show=held" replace />} />
          <Route path="/dashboard" element={<Navigate to="/system" replace />} />
          <Route path="/scans/:scanId" element={<OldLink prefix="scan-" />} />
          <Route path="/archive/:scanId" element={<OldLink prefix="doc-" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <nav className="safe-bottom fixed inset-x-0 bottom-0 z-10 grid grid-cols-3 border-t border-slate-200 bg-white sm:hidden dark:border-slate-800 dark:bg-slate-950" aria-label="Main mobile">
        {NAV.map((item) => <NavItem key={item.to} {...item} mobile />)}
      </nav>
    </div>
  );
}

/** /scans/scan-X and /archive/doc-X both become /documents/scan-X (a document's ID is its scan's). */
function OldLink({ prefix }: { prefix: string }) {
  const { scanId = "" } = useParams();
  const suffix = scanId.startsWith(prefix) ? scanId.slice(prefix.length) : scanId;
  return <Navigate to={`/documents/scan-${suffix}`} replace />;
}

function NavItem({ to, label, end, mobile }: { to: string; label: string; end?: boolean; mobile?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => cx(
      mobile ? "flex min-h-14 items-center justify-center text-sm" : "rounded-lg px-3 py-2 text-sm",
      isActive ? "font-semibold text-slate-900 dark:text-white" + (mobile ? "" : " bg-slate-100 dark:bg-slate-800")
        : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white")}>
      {label}
    </NavLink>
  );
}

type Theme = "system" | "light" | "dark";
const THEME_ICON: Record<Theme, string> = { system: "◐", light: "☀", dark: "☾" };
const THEME_NAME: Record<Theme, string> = { system: "Theme: same as the system", light: "Theme: light", dark: "Theme: dark" };

/** Cycles system → light → dark. Only an explicit choice is stored, in this browser. */
function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { const saved = localStorage.getItem("pgw-theme"); return saved === "light" || saved === "dark" ? saved : "system"; }
    catch { return "system"; }
  });
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && media.matches));
    apply();
    try { if (theme === "system") localStorage.removeItem("pgw-theme"); else localStorage.setItem("pgw-theme", theme); } catch { /* storage blocked */ }
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  return (
    <Button variant="ghost" className="px-3" title={THEME_NAME[theme]} aria-label={THEME_NAME[theme]} onClick={() => setTheme(next)} data-testid="theme">
      <span aria-hidden>{THEME_ICON[theme]}</span>
    </Button>
  );
}
