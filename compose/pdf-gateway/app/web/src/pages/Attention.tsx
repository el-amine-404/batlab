import { CheckCircle2, FilePen, ShieldAlert } from "lucide-react";
import { Link } from "react-router";
import { Preview } from "../components/progress";
import { Badge, Card, ErrorNote, PageHeader, formatWhen } from "../components/ui";
import { useAttention } from "../library";
import { statusWord } from "../words";
import { folderOf } from "./Documents";

/** The one place to look at regularly: what the gateway couldn't finish without you. */
export function AttentionPage() {
  const attention = useAttention();
  const row = "-mx-3 flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-accent/60";
  return (
    <div className="space-y-6">
      <PageHeader title="Needs your attention" description="Files the checks held back, and documents nothing could file. Everything else is taken care of." />
      <ErrorNote error={attention.error} />
      {!attention.loading && attention.count === 0 && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-border bg-card py-16 text-center backdrop-blur-xl" data-testid="attention-empty">
          <CheckCircle2 className="size-10 text-success" />
          <p className="font-medium">All clear: nothing needs you.</p>
        </div>
      )}
      {attention.held.length > 0 && (
        <Card title={`Held, blocked or not processed (${attention.held.length})`} icon={<ShieldAlert className="size-5" />}
          description="Nothing from these reached your archive. Open one to see why, then delete it or upload it again.">
          <ul className="divide-y divide-border" data-testid="attention-held">
            {attention.held.map((scan) => {
              const word = statusWord(scan.status);
              return (
                <li key={scan.scanId}>
                  <Link to={`/documents/${scan.scanId}`} className={row}>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{scan.displayName}</span>
                      <span className="text-xs text-muted-foreground">{formatWhen(scan.createdAt)}</span>
                    </span>
                    <Badge tone={word.tone}>{word.text}</Badge>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      {attention.unfiled.length > 0 && (
        <Card title={`Not filed yet (${attention.unfiled.length})`} icon={<FilePen className="size-5" />}
          description="Saved safely, but no rule or AI answer was sure enough to file them. Open one and give it a title and section, or re-run the AI.">
          <ul className="divide-y divide-border" data-testid="attention-unfiled">
            {attention.unfiled.map((doc) => (
              <li key={doc.documentId}>
                <Link to={doc.scanId ? `/documents/${doc.scanId}` : "/documents"} className={row}>
                  <Preview documentId={doc.documentId} version={doc.currentVersion} size="small" className="h-14 w-10 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{doc.title}</span>
                    <span className="block truncate text-xs text-muted-foreground">{folderOf(doc.archivePath)} · {formatWhen(doc.publishedAt)}</span>
                  </span>
                  <Badge tone="warn">To file</Badge>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
