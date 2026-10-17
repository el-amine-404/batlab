import { Download } from "lucide-react";

import type { Kind } from "@/lib/api";
import { useFiles } from "@/lib/queries";
import { formatBytes } from "@/lib/utils";

/**
 * The files the libraries hold of a video, to download from here: no need to
 * open the service that fetched them. kind narrows to the song or the video.
 */
export function Downloads({ videoId, kind, enabled = true }: { videoId: string; kind?: Kind; enabled?: boolean }) {
  const { data } = useFiles(videoId, enabled);
  const files = (data?.files ?? []).filter((file) => !kind || file.kind === kind);
  if (!files.length) return null;
  return (
    <ul className="flex flex-col gap-1">
      {files.map((file) => {
        const ext = file.name.includes(".") ? file.name.split(".").pop()!.toUpperCase() : "";
        return (
          <li key={file.url}>
            <a href={file.url} download={file.name}
               className="flex items-center gap-2.5 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-sm transition hover:border-success/40 hover:bg-accent">
              <Download className="size-4 shrink-0 text-success" />
              <span className="min-w-0 flex-1 truncate" title={file.name}>Download {file.kind === "audio" ? "the song" : "the video"}</span>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {[ext, file.size ? formatBytes(file.size) : null].filter(Boolean).join(" · ")}
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
