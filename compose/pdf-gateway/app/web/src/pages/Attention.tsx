import { Link } from "react-router-dom";
import { Preview } from "../components/progress";
import { Badge, Card, ErrorNote, Muted, formatWhen } from "../components/ui";
import { useAttention } from "../library";
import { statusWord } from "../words";
import { folderOf } from "./Documents";

/** The one place to look at regularly: what the gateway couldn't finish without you. */
export function AttentionPage() {
  const attention = useAttention();
  const row = "-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/60";
  return (
    <>
      <div>
        <h1 className="text-xl font-semibold">Needs your attention</h1>
        <Muted>Files the checks held back, and documents nothing could file. Everything else is taken care of.</Muted>
      </div>
      <ErrorNote error={attention.error} />
      {!attention.loading && attention.count === 0 && (
        <Card><p className="py-6 text-center text-sm" data-testid="attention-empty">All clear: nothing needs you.</p></Card>
      )}
      {attention.held.length > 0 && (
        <Card title={`Held, blocked or not processed (${attention.held.length})`}>
          <Muted className="mb-2">Nothing from these reached your archive. Open one to see why, then delete it or upload it again.</Muted>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="attention-held">
            {attention.held.map((scan) => {
              const word = statusWord(scan.status);
              return (
                <li key={scan.scanId}>
                  <Link to={`/documents/${scan.scanId}`} className={row}>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{scan.displayName}</span>
                      <span className="text-xs text-slate-500 dark:text-slate-400">{formatWhen(scan.createdAt)}</span>
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
        <Card title={`Not filed yet (${attention.unfiled.length})`}>
          <Muted className="mb-2">Saved safely, but no rule or AI answer was sure enough to file them. Open one and give it a title and section, or re-run the AI.</Muted>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="attention-unfiled">
            {attention.unfiled.map((doc) => (
              <li key={doc.documentId}>
                <Link to={doc.scanId ? `/documents/${doc.scanId}` : "/documents"} className={row}>
                  <Preview documentId={doc.documentId} version={doc.currentVersion} size="small" className="h-14 w-10 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{doc.title}</span>
                    <span className="block truncate text-xs text-slate-500 dark:text-slate-400">{folderOf(doc.archivePath)} · {formatWhen(doc.publishedAt)}</span>
                  </span>
                  <Badge tone="warn">To file</Badge>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
