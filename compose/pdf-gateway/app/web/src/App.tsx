import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Shell, Sidebar, useConnection } from "@batlab/ui";
import { Activity, FilePlus2, Files, FileUp, Inbox, ListTodo } from "lucide-react";
import { createBrowserRouter, Navigate, useLocation, useNavigate, useOutletContext, useParams } from "react-router";
import { api, ApiError, setCsrf, type Session } from "./api";
import { Spinner } from "./components/ui";
import { useAttention } from "./library";
import { UploadProvider, useUploads } from "./uploads";
import { LoginPage } from "./pages/Login";
import { UploadPage } from "./pages/Upload";
import { DocumentsPage } from "./pages/Documents";
import { DocumentPage } from "./pages/Document";
import { AttentionPage } from "./pages/Attention";
import { SystemPage } from "./pages/System";
import { SettingsPage } from "./pages/Settings";
import { ActivityPage } from "./pages/Activity";

const ROLE: Record<Session["role"], string> = { ADMIN: "Admin", USER: "User", READ_ONLY: "Read-only" };

/** The signed-in person, for the screens (the frame's Outlet hands it down). */
export function useSession() {
  return useOutletContext<Session>();
}

export function Logo({ className = "size-8" }: { className?: string }) {
  return <img src="/icon.svg" alt="" width={32} height={32} className={`${className} rounded-lg`} />;
}

const loading = <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Spinner /> Loading…</p>;

/** Signed in: the frame shared by batlab's apps; otherwise the sign-in (or first-admin) screen. */
function Root() {
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
  // Asked only when nobody is signed in: whether the page makes the first admin instead.
  const setup = useQuery({ queryKey: ["setup"], queryFn: api.setupState, enabled: session.isSuccess && !session.data });

  if (session.isPending || (!session.data && setup.isPending)) return loading;
  if (!session.data) return <LoginPage setup={!!setup.data?.needed} suggested={setup.data?.suggestedUsername ?? null}
    onLogin={(value) => { setCsrf(value.csrfToken); client.removeQueries({ queryKey: ["setup"] }); client.setQueryData(["session"], value); }} />;

  const logout = async () => {
    try { await api.logout(); } finally {
      setCsrf(null);
      // Sign out first (the frame observes this query), then drop every other cached response.
      client.setQueryData(["session"], null);
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "session" });
    }
  };

  return (
    <UploadProvider canUpload={session.data.role !== "READ_ONLY"}>
      <Frame session={session.data} onLogout={logout} />
    </UploadProvider>
  );
}

function Frame({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const attention = useAttention();
  // Files being checked and jobs for the AI laptop: counted beside Activity while there are any.
  const activity = useQuery({ queryKey: ["activity"], queryFn: api.activity, refetchInterval: 15_000 });
  const busy = (activity.data?.files.length ?? 0) + (activity.data?.ai.filter((job) => job.active).length ?? 0);
  // Any public, cheap answer says the page reaches the gateway.
  const live = useConnection("/api/v1/session/setup");
  return (
    <>
      <Shell name="PDF Gateway" logo={<Logo />} loading={loading} width={(path) => path.startsWith("/documents/") ? "max-w-5xl" : "max-w-6xl"}
        context={session}
        sidebar={(close) => (
          <Sidebar name="PDF Gateway" logo={<Logo />} onClose={close} live={live} settings="/settings" account="/settings"
            user={{ name: session.username, role: ROLE[session.role] }} onSignOut={onLogout}
            items={[
              { to: "/", label: "Add files", icon: FilePlus2 },
              { to: "/documents", label: "Documents", icon: Files },
              { to: "/attention", label: "Needs attention", icon: Inbox,
                badge: attention.count ? { count: attention.count, tone: "warning", title: `${attention.count} need attention` } : undefined },
              { to: "/activity", label: "Activity", icon: ListTodo,
                badge: busy ? { count: busy, tone: "active", title: `${busy} in progress` } : undefined },
              { to: "/system", label: "System", icon: Activity },
            ]} />
        )} />
      <DropAnywhere />
    </>
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
    <div className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm" data-testid="drop-overlay">
      <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary bg-card px-10 py-8 text-lg font-semibold">
        <FileUp className="size-10 text-primary" /> Drop to add these PDFs
      </div>
    </div>
  );
}

/** /scans/scan-X and /archive/doc-X both become /documents/scan-X (a document's ID is its scan's). */
function OldLink({ prefix }: { prefix: string }) {
  const { scanId = "" } = useParams();
  const suffix = scanId.startsWith(prefix) ? scanId.slice(prefix.length) : scanId;
  return <Navigate to={`/documents/scan-${suffix}`} replace />;
}

export const router = createBrowserRouter([
  {
    element: <Root />,
    children: [
      { path: "/", element: <UploadPage /> },
      { path: "/documents", element: <DocumentsPage /> },
      { path: "/documents/:scanId", element: <DocumentPage /> },
      { path: "/attention", element: <AttentionPage /> },
      { path: "/activity", element: <ActivityPage /> },
      { path: "/system", element: <SystemPage /> },
      { path: "/settings", element: <SettingsPage /> },
      // Addresses of the first version of this UI.
      { path: "/upload", element: <Navigate to="/" replace /> },
      { path: "/jobs", element: <Navigate to="/documents" replace /> },
      { path: "/archive", element: <Navigate to="/documents" replace /> },
      { path: "/quarantine", element: <Navigate to="/documents?show=held" replace /> },
      { path: "/dashboard", element: <Navigate to="/system" replace /> },
      { path: "/scans/:scanId", element: <OldLink prefix="scan-" /> },
      { path: "/archive/:scanId", element: <OldLink prefix="doc-" /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
