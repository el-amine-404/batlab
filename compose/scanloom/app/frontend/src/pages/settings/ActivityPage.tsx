import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/layout/PageHeader";
import { Failed, Loading } from "@/components/layout/States";
import { api, errorText } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function ActivityPage() {
  const logs = useQuery({ queryKey: ["logs"], queryFn: api.logs, refetchInterval: 5000 });
  return (
    <>
      <PageHeader title="Activity" description="What the server has been doing, newest last. Kept in memory: a restart clears it." />
      {logs.error ? <Failed message={errorText(logs.error)} /> : !logs.data ? <Loading /> : (
        <div className="max-h-[70dvh] overflow-auto rounded-xl border border-border bg-card p-3 font-mono text-xs leading-relaxed">
          {!logs.data.lines.length && <p className="text-muted-foreground">Nothing yet.</p>}
          {logs.data.lines.map((line) => (
            <div key={line.seq} className={cn(line.level === "error" ? "text-destructive" : line.level === "warning" ? "text-warning" : "text-foreground")}>
              <span className="text-muted-foreground">{line.at.slice(11, 19)}</span> {line.message}
              {line.detail && <pre className="mt-1 mb-2 whitespace-pre-wrap text-destructive">{line.detail}</pre>}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
