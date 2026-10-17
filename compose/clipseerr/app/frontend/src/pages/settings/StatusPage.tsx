import { CheckCircle2, Trash2, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, errorText } from "@/lib/api";
import { queryClient, useStatus } from "@/lib/queries";
import { timeAgo } from "@/lib/utils";

const SOURCES: Record<string, string> = { youtube: "YouTube Music", apple: "Apple Music" };

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-3 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right">{children}</span>
    </div>
  );
}

export default function StatusPage() {
  const { data, error, refetch, isPending } = useStatus();
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;

  const clear = async () => {
    try {
      await api.clearCache();
      await queryClient.invalidateQueries();
      toast.success("Cache cleared: lists are fetched again");
    } catch (caught) {
      toast.error(errorText(caught));
    }
  };

  return (
    <>
      <PageHeader eyebrow="Settings" title="Status" />
      <div className="grid gap-6 md:grid-cols-2">
        <section>
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">Sources</h2>
          <Card>
            {Object.keys(SOURCES).map((key) => {
              const source = data.sources[key];
              return (
                <Line key={key} label={SOURCES[key]}>
                  {!source ? <span className="text-muted-foreground">not asked yet</span> : source.ok ? (
                    <span className="inline-flex items-center gap-1.5 text-success"><CheckCircle2 className="size-4" /> {source.ms} ms, {timeAgo(source.at)}</span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-destructive" title={source.error ?? undefined}><XCircle className="size-4" /> failed {timeAgo(source.at)}</span>
                  )}
                </Line>
              );
            })}
          </Card>
        </section>
        <section>
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">Requests</h2>
          <Card>
            <Line label="Under way">{data.requests.active}</Line>
            <Line label="Done">{data.requests.completed}</Line>
            <Line label="Failed">{data.requests.failed}</Line>
            <Line label="Last look at the services">{data.last_round ? timeAgo(data.last_round) : "not yet"}</Line>
          </Card>
        </section>
        <section>
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">Where requests go</h2>
          <Card>
            <Line label="Songs">{data.chain.audio.join(" → ") || <span className="text-destructive">no service</span>}</Line>
            <Line label="Videos">{data.chain.video.join(" → ") || <span className="text-destructive">no service</span>}</Line>
          </Card>
        </section>
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted-foreground">Cache</h2>
            <Button variant="ghost" size="sm" onClick={clear}><Trash2 /> Clear</Button>
          </div>
          <Card>
            <Line label="Answers kept">{data.cache.entries}</Line>
            <Line label="Served from the cache">{data.cache.hits} of {data.cache.hits + data.cache.misses}</Line>
            <Line label="clipseerr">v{data.version}, up {timeAgo(data.started_at).replace(" ago", "")}</Line>
            <Line label="ytmusicapi">{data.ytmusicapi}</Line>
          </Card>
        </section>
      </div>
    </>
  );
}
