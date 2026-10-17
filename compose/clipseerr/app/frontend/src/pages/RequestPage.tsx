import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Clock3, Download, Send, X } from "lucide-react";
import { Link, useParams } from "react-router";

import { Failed, Loading } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/video/StatusBadge";
import { Thumb } from "@/components/video/Thumb";
import { ACTIVE, api, type Attempt } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

import { RequestActions } from "./RequestsPage";

const ICON = { sent: Send, downloading: Download, completed: Check, failed: X } as const;

function Step({ attempt }: { attempt: Attempt }) {
  const Icon = ICON[attempt.state] ?? Clock3;
  return (
    <li className="relative flex gap-3 pb-5 last:pb-0">
      <span className={cn("relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border",
                          attempt.state === "failed" ? "border-destructive/40 bg-destructive/10 text-destructive"
                            : attempt.state === "completed" ? "border-success/40 bg-success/10 text-success"
                              : "border-rose-400/40 bg-rose-400/10 text-rose-200")}>
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1 pt-1">
        <p className="text-sm font-medium">
          {attempt.service_name} <span className="font-normal text-muted-foreground">· {attempt.service_type}</span>
        </p>
        {attempt.message && <p className="text-sm text-muted-foreground">{attempt.message}</p>}
        <p className="mt-0.5 text-xs text-muted-foreground/70">
          {[attempt.quality, `asked ${timeAgo(attempt.started_at)}`, attempt.updated_at !== attempt.started_at && `last news ${timeAgo(attempt.updated_at)}`,
            attempt.external_id && `id ${attempt.external_id.length > 40 ? `${attempt.external_id.slice(0, 40)}…` : attempt.external_id}`]
            .filter(Boolean).join(" · ")}
        </p>
      </div>
    </li>
  );
}

export function RequestPage() {
  const { id = "" } = useParams();
  const { data, error, refetch, isPending } = useQuery({
    queryKey: ["requests", "one", id],
    queryFn: ({ signal }) => api.request(id, signal),
    staleTime: 0,
    refetchInterval: (query) => (query.state.data && ACTIVE.includes(query.state.data.status) ? 3000 : false),
  });
  if (isPending) return <Loading />;
  if (error) return <Failed error={error} retry={() => void refetch()} />;
  const rounds = [...new Set(data.attempts.map((a) => a.round))].sort((a, b) => b - a);

  return (
    <div className="mx-auto max-w-3xl">
      <Button variant="ghost" size="sm" asChild className="mb-4 -ml-2">
        <Link to="/requests"><ArrowLeft /> Requests</Link>
      </Button>
      <div className="flex flex-col gap-5 sm:flex-row">
        <Link to={`/watch/${data.media.video_key}`} className="w-full shrink-0 overflow-hidden rounded-xl ring-1 ring-white/10 sm:w-64">
          <Thumb src={data.media.thumbnail} youtubeId={data.media.video_id} size="hq" eager />
        </Link>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium tracking-wider text-rose-300/90 uppercase">{data.kind === "audio" ? "Song" : "Video"} request</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            <Link to={`/watch/${data.media.video_key}`} className="underline-offset-4 hover:text-rose-100 hover:underline">{data.media.title}</Link>
          </h1>
          <p className="text-muted-foreground">
            {data.media.artist_id ? (
              <Link to={`/artist/${data.media.artist_id}`} className="font-medium text-foreground/85 underline-offset-4 hover:text-rose-200 hover:underline">
                {data.media.artist}
              </Link>
            ) : data.media.artist}
            {data.media.album && ` · ${data.media.album}`}{data.media.year && ` · ${data.media.year}`}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            {data.quality && <span className="text-sm font-medium">{data.quality}</span>}
            <span className="text-xs text-muted-foreground">asked {timeAgo(data.created_at)}</span>
          </div>
          {data.message && <p className="mt-2 text-sm text-muted-foreground">{data.message}</p>}
          <div className="mt-4"><RequestActions request={data} /></div>
        </div>
      </div>

      <h2 className="mt-10 mb-4 text-lg font-semibold tracking-tight">Services asked</h2>
      {!data.attempts.length ? (
        <p className="text-sm text-muted-foreground">No service has it yet.</p>
      ) : (
        rounds.map((round) => (
          <section key={round} className="mb-6">
            {rounds.length > 1 && <p className="mb-3 text-xs font-medium text-muted-foreground">{round === data.round ? "This time" : `Try ${round + 1}`}</p>}
            <ol className="relative before:absolute before:top-4 before:bottom-4 before:left-4 before:w-px before:bg-border">
              {data.attempts.filter((a) => a.round === round).map((attempt, i) => (
                <Step key={i} attempt={attempt} />
              ))}
            </ol>
          </section>
        ))
      )}
    </div>
  );
}
