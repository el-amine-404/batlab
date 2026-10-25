import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, setCsrf, type Session } from "./api";
import { Button, cx } from "./components/ui";
import { LoginPage } from "./pages/Login";
import { UploadPage } from "./pages/Upload";
import { JobsPage } from "./pages/Jobs";
import { ScanDetailPage } from "./pages/ScanDetail";
import { QuarantinePage } from "./pages/Quarantine";
import { ArchivePage, DocumentPage } from "./pages/Archive";
import { DashboardPage } from "./pages/Dashboard";

const NAV = [
  { to: "/upload", label: "Upload" },
  { to: "/jobs", label: "Jobs" },
  { to: "/archive", label: "Archive" },
  { to: "/quarantine", label: "Quarantine" },
  { to: "/dashboard", label: "Status" },
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
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-2">
          <span className="font-semibold">PDF Gateway</span>
          <nav className="hidden gap-1 sm:flex" aria-label="Main">
            {NAV.map((item) => <NavItem key={item.to} {...item} />)}
          </nav>
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <span className="hidden sm:inline" data-testid="whoami">{session.data.username}</span>
            <Button variant="ghost" onClick={logout}>Sign out</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl space-y-4 px-4 py-4">
        <Routes>
          <Route path="/" element={<Navigate to="/upload" replace />} />
          <Route path="/upload" element={<UploadPage session={session.data} />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/scans/:scanId" element={<ScanDetailPage />} />
          <Route path="/archive" element={<ArchivePage />} />
          <Route path="/archive/:documentId" element={<DocumentPage session={session.data} />} />
          <Route path="/quarantine" element={<QuarantinePage session={session.data} />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/upload" replace />} />
        </Routes>
      </main>
      <nav className="safe-bottom fixed inset-x-0 bottom-0 z-10 grid grid-cols-5 border-t border-slate-200 bg-white sm:hidden" aria-label="Main mobile">
        {NAV.map((item) => <NavItem key={item.to} {...item} mobile />)}
      </nav>
    </div>
  );
}

function NavItem({ to, label, mobile }: { to: string; label: string; mobile?: boolean }) {
  return (
    <NavLink to={to} className={({ isActive }) => cx(
      mobile ? "flex min-h-14 items-center justify-center text-xs" : "rounded-lg px-3 py-2 text-sm",
      isActive ? "font-semibold text-slate-900" + (mobile ? "" : " bg-slate-100") : "text-slate-600 hover:text-slate-900")}>
      {label}
    </NavLink>
  );
}
