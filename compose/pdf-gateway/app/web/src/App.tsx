import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, setCsrf, type Session } from "./api";
import { Button, cx } from "./components/ui";
import { BrowseTree } from "./components/BrowseTree";
import { useAttention } from "./library";
import { UploadProvider, useUploads } from "./uploads";
import { LoginPage } from "./pages/Login";
import { UploadPage } from "./pages/Upload";
import { DocumentsPage } from "./pages/Documents";
import { DocumentPage } from "./pages/Document";
import { AttentionPage } from "./pages/Attention";
import { SystemPage } from "./pages/System";

const NAV = [
  { to: "/", label: "Add files", short: "Add", end: true },
  { to: "/documents", label: "Documents", short: "Documents" },
  { to: "/attention", label: "Needs attention", short: "Attention", count: true },
  { to: "/system", label: "System", short: "System" },
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
    <UploadProvider canUpload={session.data.role !== "READ_ONLY"}>
      <Shell session={session.data} onLogout={logout} />
    </UploadProvider>
  );
}

function Shell({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const attention = useAttention();
  return (
    <div className="min-h-dvh pb-20 sm:pb-0 lg:grid lg:grid-cols-[16rem_minmax(0,1fr)]">
      {/* Wide screens: the sidebar holds the navigation and the archive's sections. */}
      <aside className="sticky top-0 hidden h-dvh flex-col gap-4 overflow-y-auto border-r border-slate-200 bg-white px-3 py-4 lg:flex dark:border-slate-800 dark:bg-slate-950" aria-label="Sidebar">
        <Brand />
        <nav className="flex flex-col gap-0.5" aria-label="Sidebar navigation">
          {NAV.map((item) => <NavItem key={item.to} {...item} count={item.count ? attention.count : undefined} side />)}
        </nav>
        <div className="min-h-0 flex-1">
          <p className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Your archive</p>
          <BrowseTree />
        </div>
        <div className="flex items-center gap-1 border-t border-slate-200 pt-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-400">
          <ThemeSwitch />
          <span className="min-w-0 flex-1 truncate px-2" data-testid="whoami">{session.username}</span>
          <Button variant="ghost" onClick={onLogout}>Sign out</Button>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur lg:hidden dark:border-slate-800 dark:bg-slate-950/90">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-2">
            <Brand />
            <nav className="hidden gap-1 sm:flex" aria-label="Main">
              {NAV.map((item) => <NavItem key={item.to} {...item} label={item.short} count={item.count ? attention.count : undefined} />)}
            </nav>
            <div className="flex items-center gap-1 text-sm text-slate-600 dark:text-slate-400">
              <ThemeSwitch />
              <Button variant="ghost" onClick={onLogout}>Sign out</Button>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-5xl space-y-4 px-4 py-4 lg:px-8 lg:py-6">
          <Routes>
            <Route path="/" element={<UploadPage session={session} />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/documents/:scanId" element={<DocumentPage session={session} />} />
            <Route path="/attention" element={<AttentionPage />} />
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
      </div>
      <nav className="safe-bottom fixed inset-x-0 bottom-0 z-10 grid grid-cols-4 border-t border-slate-200 bg-white sm:hidden dark:border-slate-800 dark:bg-slate-950" aria-label="Main mobile">
        {NAV.map((item) => <NavItem key={item.to} {...item} label={item.short} count={item.count ? attention.count : undefined} mobile />)}
      </nav>
      <DropAnywhere />
    </div>
  );
}

/** The logo and the name: both go to the main page. */
function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2 rounded-lg px-1 py-1 font-semibold hover:opacity-80 lg:px-3" data-testid="home-link" aria-label="PDF Gateway, main page">
      <img src="/icon.svg" alt="" width={28} height={28} className="size-7 rounded-md" />
      <span>PDF Gateway</span>
    </Link>
  );
}

/** Files dropped on any page are added (then shown on the main page). */
function DropAnywhere() {
  const { add, canUpload } = useUploads();
  const navigate = useNavigate();
  const location = useLocation();
  const [over, setOver] = useState(false);
  useEffect(() => {
    if (!canUpload) return;
    let depth = 0;
    const hasFiles = (event: DragEvent) => !!event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files");
    const enter = (event: DragEvent) => { if (hasFiles(event)) { depth += 1; setOver(true); } };
    const leave = (event: DragEvent) => { if (hasFiles(event)) { depth = Math.max(0, depth - 1); if (!depth) setOver(false); } };
    const overHandler = (event: DragEvent) => { if (hasFiles(event)) event.preventDefault(); };
    const drop = (event: DragEvent) => {
      depth = 0; setOver(false);
      if (!hasFiles(event) || event.defaultPrevented) return; // the main page's own drop area took it
      event.preventDefault();
      add(Array.from(event.dataTransfer!.files));
      if (location.pathname !== "/") navigate("/");
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", overHandler);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter); window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", overHandler); window.removeEventListener("drop", drop);
    };
  }, [add, canUpload, location.pathname, navigate]);
  if (!over || location.pathname === "/") return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6" data-testid="drop-overlay">
      <div className="rounded-2xl border-2 border-dashed border-white bg-slate-900/80 px-8 py-6 text-lg font-semibold text-white">Drop to add these PDFs</div>
    </div>
  );
}

/** /scans/scan-X and /archive/doc-X both become /documents/scan-X (a document's ID is its scan's). */
function OldLink({ prefix }: { prefix: string }) {
  const { scanId = "" } = useParams();
  const suffix = scanId.startsWith(prefix) ? scanId.slice(prefix.length) : scanId;
  return <Navigate to={`/documents/scan-${suffix}`} replace />;
}

function NavItem({ to, label, end, mobile, side, count }: { to: string; label: string; end?: boolean; mobile?: boolean; side?: boolean; count?: number }) {
  return (
    <NavLink to={to} end={end} data-testid={`nav-${to === "/" ? "home" : to.slice(1)}`} className={({ isActive }) => cx(
      mobile ? "flex min-h-14 items-center justify-center gap-1 text-sm" : side ? "flex items-center justify-between rounded-lg px-3 py-2 text-sm" : "flex items-center gap-1 rounded-lg px-3 py-2 text-sm",
      isActive ? "font-semibold text-slate-900 dark:text-white" + (mobile ? "" : " bg-slate-100 dark:bg-slate-800")
        : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white")}>
      {label}
      {!!count && <span className="rounded-full bg-amber-500 px-1.5 text-xs font-semibold text-white" data-testid="attention-count" aria-label={`${count} need attention`}>{count}</span>}
    </NavLink>
  );
}

type Theme = "system" | "light" | "dark";
const THEME_LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const THEME_NAME: Record<Theme, string> = { system: "Theme: same as the system (click for light)", light: "Theme: light (click for dark)", dark: "Theme: dark (click for automatic)" };

function ThemeIcon({ theme }: { theme: Theme }) {
  const common = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, "aria-hidden": true };
  if (theme === "light") return <svg {...common}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
  if (theme === "dark") return <svg {...common}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
  return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" /></svg>;
}

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
      <ThemeIcon theme={theme} /><span className="hidden text-xs sm:inline">{THEME_LABEL[theme]}</span>
    </Button>
  );
}
