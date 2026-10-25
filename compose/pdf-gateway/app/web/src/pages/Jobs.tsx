import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorNote, StatusBadge, TERMINAL, formatBytes, formatDate } from "../components/ui";

export function JobsPage() {
  const scans = useQuery({
    queryKey: ["scans"],
    queryFn: () => api.scans(),
    // Poll only while something is still moving; idle pages make no requests.
    refetchInterval: (query) => query.state.data?.items.some((scan) => !TERMINAL.has(scan.status)) ? 3_000 : false,
  });
  return (
    <Card title="Jobs">
      <ErrorNote error={scans.error} />
      {scans.data?.items.length === 0 && <p className="text-sm text-slate-500">No uploads yet.</p>}
      <ul className="divide-y divide-slate-100" data-testid="job-list">
        {scans.data?.items.map((scan) => (
          <li key={scan.scanId}>
            <Link to={`/scans/${scan.scanId}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3 hover:bg-slate-50">
              <span className="min-w-0 flex-1 truncate text-sm font-medium" title={scan.displayName}>{scan.displayName}</span>
              <StatusBadge status={scan.status} />
              <span className="w-full text-xs text-slate-500 sm:w-auto">{formatBytes(scan.sourceBytes)} · {formatDate(scan.createdAt)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
