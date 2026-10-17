import { Ban, Film, Inbox, Music2, RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Empty, Failed, Loading, PageHeader } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { AttemptTrail } from "@/components/video/AttemptTrail";
import { StatusBadge } from "@/components/video/StatusBadge";
import { Thumb } from "@/components/video/Thumb";
import { ACTIVE, type Request } from "@/lib/api";
import { useRequestAction, useRequests } from "@/lib/queries";
import { cn, timeAgo } from "@/lib/utils";

const TABS = [
  { value: "active", label: "Under way" },
  { value: "completed", label: "Done" },
  { value: "failed", label: "Failed" },
  { value: "all", label: "All" },
] as const;

export function RequestActions({ request, compact = false }: { request: Request; compact?: boolean }) {
  const action = useRequestAction();
  const busy = action.isPending;
  const size = compact ? "icon-sm" : "sm";
  const active = ACTIVE.includes(request.status);
  return (
    <div className="flex items-center gap-1">
      {!active && request.status !== "completed" && (
        <Button size={size} variant="secondary" disabled={busy} title="Ask every service again"
                onClick={() => action.mutate({ id: request.id, action: "retry" })}>
          <RotateCw /> {!compact && "Ask again"}
        </Button>
      )}
      {request.status === "completed" && !compact && (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => action.mutate({ id: request.id, action: "retry" })}>
          <RotateCw /> Get it again
        </Button>
      )}
      {active ? (
        <Button size={size} variant="ghost" disabled={busy} title="Stop following it"
                onClick={() => action.mutate({ id: request.id, action: "cancel" })}>
          <Ban /> {!compact && "Cancel"}
        </Button>
      ) : (
        <Button size={size} variant="ghost" disabled={busy} title="Remove from the list"
                onClick={() => action.mutate({ id: request.id, action: "remove" })}>
          <Trash2 /> {!compact && "Remove"}
        </Button>
      )}
    </div>
  );
}

function current(request: Request) {
  return [...request.attempts].reverse().find((a) => a.round === request.round);
}

function Row({ request }: { request: Request }) {
  const attempt = current(request);
  const KindIcon = request.kind === "audio" ? Music2 : Film;
  return (
    <li className="flex items-center gap-3 rounded-xl border border-border bg-card p-2.5 pr-3 sm:gap-4">
      <Link to={`/watch/${request.media.video_key}`} className="w-24 shrink-0 overflow-hidden rounded-lg sm:w-32">
        <Thumb src={request.media.thumbnail} youtubeId={request.media.video_id} />
      </Link>
      <div className="min-w-0 flex-1">
        <Link to={`/requests/${request.id}`} className="block min-w-0 hover:underline hover:underline-offset-4">
          <p className="truncate text-sm font-medium">{request.media.title}</p>
          <p className="truncate text-xs text-muted-foreground">{request.media.artist}</p>
        </Link>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <KindIcon className="size-3.5" /> {request.kind === "audio" ? "Song" : "Video"}
          </span>
          <StatusBadge status={request.status} service={attempt?.service_name} />
          {request.quality && <span className="text-xs font-medium text-foreground/80">{request.quality}</span>}
          <span className="hidden sm:inline"><AttemptTrail request={request} /></span>
        </div>
        {request.status === "downloading" && typeof attempt?.progress === "number" && (
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-secondary">
            <div className="h-full rounded-full bg-gradient-to-r from-rose-500 to-orange-300 transition-[width]" style={{ width: `${Math.round(attempt.progress * 100)}%` }} />
          </div>
        )}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <span className="text-[11px] text-muted-foreground tabular-nums">{timeAgo(request.created_at)}</span>
        <RequestActions request={request} compact />
      </div>
    </li>
  );
}

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.value === params.get("show"))?.value ?? "all";
  const [filter, setFilter] = useState("");
  const { data, error, refetch, isPending } = useRequests(tab, filter.trim());

  return (
    <>
      <PageHeader eyebrow="Requests" title="What was asked for"
                  description="Each request goes to the services in order, and on to the next when one fails." />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-xl border border-border bg-black/20 p-1" role="tablist">
          {TABS.map((item) => {
            const count = data?.counts[item.value];
            return (
              <button key={item.value} type="button" role="tab" aria-selected={tab === item.value}
                      onClick={() => setParams(item.value === "all" ? {} : { show: item.value }, { replace: true })}
                      className={cn("flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-muted-foreground transition",
                                    tab === item.value ? "bg-accent text-foreground" : "hover:text-foreground")}>
                {item.label}
                {!!count && <span className="text-xs tabular-nums text-muted-foreground">{count}</span>}
              </button>
            );
          })}
        </div>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by title or artist"
               className="h-9 w-full rounded-lg border border-input bg-black/20 px-3 text-sm outline-none focus-visible:border-ring sm:w-64" />
      </div>
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failed error={error} retry={() => void refetch()} />
      ) : !data.requests.length ? (
        <Empty icon={<Inbox />} title={filter ? "Nothing matches" : tab === "active" ? "Nothing under way" : "No requests yet"}>
          {!filter && <>Open a video from <Link to="/" className="text-rose-300 hover:underline">Discover</Link> and get its song or the video.</>}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.requests.map((request) => (
            <Row key={request.id} request={request} />
          ))}
        </ul>
      )}
    </>
  );
}
