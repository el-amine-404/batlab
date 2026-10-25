import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Scan, type Session } from "../api";
import { Badge, Button, Card, ErrorNote, StatusBadge, formatDate } from "../components/ui";

/** Held documents. Originals are never previewed; administrators may delete them. */
export function QuarantinePage({ session }: { session: Session }) {
  const client = useQueryClient();
  const held = useQuery({ queryKey: ["quarantine"], queryFn: api.quarantine });
  const remove = useMutation({
    mutationFn: (scan: Scan) => api.deleteQuarantined(scan.scanId, scan.originalRetention === "SIGNED_RESTRICTED"),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["quarantine"] }); client.invalidateQueries({ queryKey: ["scans"] }); },
  });
  const confirmDelete = (scan: Scan) => {
    const signed = scan.originalRetention === "SIGNED_RESTRICTED";
    const message = signed
      ? "This original is retained signed evidence. Delete it permanently?"
      : "Delete this quarantined original permanently?";
    if (window.confirm(message)) remove.mutate(scan);
  };

  return (
    <Card title="Quarantine">
      <p className="mb-3 text-sm text-slate-600">Documents held for review, blocked or failed. Originals stay in restricted storage and are never shown here.</p>
      <ErrorNote error={held.error ?? remove.error} />
      {held.data?.items.length === 0 && <p className="text-sm text-slate-500">Nothing is held.</p>}
      <ul className="divide-y divide-slate-100" data-testid="quarantine-list">
        {held.data?.items.map((scan) => (
          <li key={scan.scanId} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 text-sm">
            <Link to={`/scans/${scan.scanId}`} className="min-w-0 flex-1 truncate font-medium underline" title={scan.displayName}>{scan.displayName}</Link>
            <StatusBadge status={scan.status} />
            {scan.originalRetention === "SIGNED_RESTRICTED" && <Badge tone="info">signed original</Badge>}
            <span className="text-xs text-slate-500">{formatDate(scan.updatedAt)}</span>
            {session.role === "ADMIN" && (
              <Button variant="danger" className="min-h-9 px-3" disabled={remove.isPending} onClick={() => confirmDelete(scan)}>Delete</Button>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-slate-500">Overrides and rescans are not available yet.</p>
    </Card>
  );
}
