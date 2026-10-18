"""One download, in a process of its own (python -m videodrop.runner): the
server stays responsive whatever yt-dlp and ffmpeg do, a download is stopped
by stopping its process (and its ffmpeg with it), and a crash ends only it.

stdin: one JSON object {"url", "work_dir", "options", "output", "rate_limit",
"max_filesize", "cookies", "deno"}. stdout: events, one JSON object a line
(EVENTS); anything else the process prints goes to stderr, the container's
log. Exit 0 when at least one file was made, 1 otherwise."""

import json
import os
import sys
import time
from pathlib import Path

# Events: {"event": name, ...}.
#   item      {index, total, id, title, uploader, thumbnail, duration}  a video starts
#   progress  {index, downloaded, total, speed, eta}                     at most twice a second
#   stage     {stage}                                                    downloading, processing
#   file      {path}                                                     a finished file, relative to work_dir
#   warning   {message}
#   error     {message}                                                  a video failed (the job may go on)
EVENTS = ("item", "progress", "stage", "file", "warning", "error")
PROGRESS_EVERY = 0.5


class Emitter:
    def __init__(self, stream):
        self._stream = stream

    def __call__(self, event: str, **fields) -> None:
        self._stream.write(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str) + "\n")
        self._stream.flush()


def _stdout_for_events():
    """The real stdout, for events only; fd 1 then points at stderr, so that
    nothing else (yt-dlp, ffmpeg, a library's print) can garble them."""
    events = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return events


def main() -> int:
    emit = Emitter(_stdout_for_events())
    config = json.load(sys.stdin)

    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError

    from .options import VideoOptions
    from .ytdlp import params

    work_dir = Path(config["work_dir"])
    options = VideoOptions.model_validate(config["options"])
    made: list[str] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    last = [0.0]

    class Logger:
        def debug(self, message):
            pass

        def info(self, message):
            pass

        def warning(self, message):
            print(f"WARNING: {message}", file=sys.stderr, flush=True)
            emit("warning", message=message[:500])

        def error(self, message):
            print(message, file=sys.stderr, flush=True)
            errors.append(message)
            emit("error", message=message[:1000])

    def item(info: dict) -> int:
        """The video's index in the job; an item event the first time."""
        key = info.get("id") or info.get("webpage_url") or "?"
        if key not in seen:
            seen[key] = len(seen)
            emit("item", index=seen[key], total=info.get("n_entries") or 1, id=info.get("id"),
                 title=info.get("title"), uploader=info.get("artist") or info.get("uploader") or info.get("channel"),
                 thumbnail=info.get("thumbnail"), duration=info.get("duration"))
        return seen[key]

    def progress(status: dict) -> None:
        info = status.get("info_dict") or {}
        index = item(info)
        if status.get("status") == "downloading":
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY:
                return
            last[0] = now
            emit("progress", index=index, downloaded=status.get("downloaded_bytes") or 0,
                 total=status.get("total_bytes") or status.get("total_bytes_estimate"),
                 speed=status.get("speed"), eta=status.get("eta"),
                 fragment=status.get("fragment_index"), fragments=status.get("fragment_count"))
        elif status.get("status") == "finished":
            emit("stage", stage="processing")

    def postprocessing(status: dict) -> None:
        if status.get("status") == "started":
            emit("stage", stage="processing")

    class Collect(PostProcessor):
        """Last step of each video: its file, where it ended up."""

        def run(self, info):
            path = info.get("filepath")
            if path and os.path.isfile(path):
                relative = os.path.relpath(path, work_dir)
                made.append(relative)
                emit("file", path=relative)
            return [], info

    ydl_params = params(options, config["output"], work_dir, rate_limit=config.get("rate_limit"),
                        max_filesize=config.get("max_filesize"), cookies=config.get("cookies"),
                        deno=config.get("deno"))
    ydl_params.update(logger=Logger(), progress_hooks=[progress], postprocessor_hooks=[postprocessing])
    emit("stage", stage="downloading")
    try:
        with yt_dlp.YoutubeDL(ydl_params) as ydl:
            ydl.add_post_processor(Collect(), when="after_move")
            ydl.download([config["url"]])
    except DownloadError as exc:
        if not errors:
            emit("error", message=str(exc)[:1000])
    except Exception as exc:  # yt-dlp's own bug: said, not hidden
        emit("error", message=f"yt-dlp failed: {exc.__class__.__name__}: {exc}"[:1000])
    if not made and not errors:
        emit("error", message="Nothing was downloaded: the file is probably over the size limit, or the link "
                              "has no video.")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
