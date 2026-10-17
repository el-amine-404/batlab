import { ArrowRight, Film, Loader2, Music2, RotateCw } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { ACTIVE, type Kind, type Request, type VideoDetail } from "@/lib/api";
import { useAsk, useRequestAction, useStatus, useVideoRequests } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { StatusBadge } from "./StatusBadge";

const COPY: Record<Kind, { title: string; icon: typeof Music2; action: string; blurb: string }> = {
  audio: { title: "The song", icon: Music2, action: "Get the song", blurb: "Into your music library, at the best quality a service finds." },
  video: { title: "The video", icon: Film, action: "Get the video", blurb: "The video file itself, at the best quality YouTube has." },
};

function current(request: Request) {
  return [...request.attempts].reverse().find((a) => a.round === request.round);
}

function KindCard({ video, kind, request, chain }: { video: VideoDetail; kind: Kind; request: Request | null | undefined; chain: string[] }) {
  const ask = useAsk(video.id);
  const action = useRequestAction();
  const copy = COPY[kind];
  const Icon = copy.icon;
  const attempt = request ? current(request) : undefined;
  const busy = ask.isPending || action.isPending;
  const unavailable = kind === "video" && !video.youtube_id;
  const noService = chain.length === 0;

  return (
    <div className={cn("flex flex-col gap-3 rounded-xl border border-border bg-card p-4", request?.status === "completed" && "border-success/25")}>
      <div className="flex items-start gap-3">
        <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg",
                            kind === "audio" ? "bg-rose-500/15 text-rose-300" : "bg-orange-400/15 text-orange-200")}>
          <Icon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{copy.title}</p>
          <p className="text-xs text-muted-foreground">{copy.blurb}</p>
        </div>
      </div>

      {request && request.status !== "cancelled" ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={request.status} service={attempt?.service_name} />
            {request.quality && <span className="text-xs font-medium text-muted-foreground">{request.quality}</span>}
          </div>
          {request.message && <p className="text-xs text-muted-foreground">{request.message}</p>}
          {request.status === "downloading" && typeof attempt?.progress === "number" && (
            <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
              <div className="h-full rounded-full bg-gradient-to-r from-rose-500 to-orange-300 transition-[width]" style={{ width: `${Math.round(attempt.progress * 100)}%` }} />
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {request.status === "failed" && (
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => action.mutate({ id: request.id, action: "retry" })}>
                <RotateCw /> Ask again
              </Button>
            )}
            <Button size="sm" variant="ghost" asChild>
              <Link to={`/requests/${request.id}`}>
                Details <ArrowRight />
              </Link>
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Button onClick={() => ask.mutate(kind)} disabled={busy || unavailable || noService} className="w-full">
            {ask.isPending ? <Loader2 className="animate-spin" /> : <Icon />}
            {copy.action}
          </Button>
          <p className="text-[11px] text-muted-foreground">
            {unavailable
              ? "Not on YouTube: there is no video file to get."
              : noService
                ? <>No service takes {kind} yet. <Link to="/settings/services" className="text-rose-300 underline-offset-2 hover:underline">Add one</Link>.</>
                : <>Via {chain.join(" → ")}{chain.length > 1 ? ", in that order" : ""}.</>}
          </p>
        </div>
      )}
    </div>
  );
}

/** Ask for the song or the video, and see where the request is. */
export function RequestPanel({ video }: { video: VideoDetail }) {
  const { data } = useVideoRequests(video.id);
  const { data: status } = useStatus();
  const chain = status?.chain ?? { audio: [], video: [] };
  const anyActive = [data?.audio, data?.video].some((r) => r && ACTIVE.includes(r.status));
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1" aria-busy={anyActive}>
      <KindCard video={video} kind="audio" request={data?.audio} chain={chain.audio} />
      <KindCard video={video} kind="video" request={data?.video} chain={chain.video} />
    </div>
  );
}
