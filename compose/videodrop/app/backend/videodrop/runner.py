"""One download, in a process of its own (python -m videodrop.runner): the
server stays responsive whatever yt-dlp and ffmpeg do, a download is stopped
by stopping its process (and its ffmpeg with it), and a crash ends only it.

stdin: one JSON object {"url", "work_dir", "options", "output", "rate_limit",
"max_filesize", "cookies", "deno", "song"}. stdout: events, one JSON object a line
(EVENTS); anything else the process prints goes to stderr, the container's
log. Exit 0 when at least one file was made, 1 otherwise."""

import json
import os
import sys
import time
from pathlib import Path

# Events: {"event": name, ...}.
#   item      {index, total, id, title, uploader, thumbnail, duration}  a video starts
#   progress  {index, downloaded, total, speed, eta, fragment, fragments} at most twice a second; bytes of
#                                                                        the video's streams together
#   stage     {stage, step}                                              downloading, processing (step: which
#                                                                        part of it: Merger, EmbedThumbnail…)
#   file      {path, checks}                                             a finished file, relative to work_dir, checked
#   note      {message}                                                  beside the download: lyrics found or not
#   warning   {message}
#   error     {message}                                                  a video failed (the job may go on)
EVENTS = ("item", "progress", "stage", "file", "note", "warning", "error")
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

    from . import checks, lyrics
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

    # A video is often two streams downloaded one after the other (picture,
    # then sound): bytes are counted over both, so the bar doesn't start again.
    parts: dict[str, list] = {}

    def expected_size(info: dict) -> float | None:
        streams = info.get("requested_formats") or [info]
        sizes = [f.get("filesize") or f.get("filesize_approx") for f in streams]
        return sum(sizes) if all(sizes) else None

    def progress(status: dict) -> None:
        info = status.get("info_dict") or {}
        index = item(info)
        key = f"{index}:{status.get('filename')}"
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        parts[key] = [index, status.get("downloaded_bytes") or 0, total]
        if status.get("status") == "finished":
            parts[key][1] = total or parts[key][1]
            return
        if status.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - last[0] < PROGRESS_EVERY:
            return
        last[0] = now
        mine = [p for p in parts.values() if p[0] == index]
        done = sum(p[1] for p in mine)
        seen = sum(p[2] or 0 for p in mine)
        # The size the site said for both streams, else what each said once it started.
        known = expected_size(info) or (seen if all(p[2] for p in mine) else None)
        emit("progress", index=index, downloaded=int(done), total=int(max(known, seen)) if known else None,
             speed=status.get("speed"), eta=status.get("eta"),
             fragment=status.get("fragment_index"), fragments=status.get("fragment_count"))

    def postprocessing(status: dict) -> None:
        if status.get("status") == "started":
            emit("stage", stage="processing", step=status.get("postprocessor"))

    def add_lyrics(path: Path, info: dict) -> None:
        song = config.get("song") or {}
        named = (song.get("artist"), song.get("title")) if song.get("artist") and song.get("title") else lyrics.guess(info)
        if not named:
            emit("note", message="No lyrics: the song couldn't be told from the video's title.")
            return
        artist, title = named
        try:
            found = lyrics.find(artist, title, info.get("duration"))
        except OSError as exc:
            emit("note", message=f"No lyrics: LRCLIB couldn't be reached ({exc.__class__.__name__}).")
            return
        if not found:
            emit("note", message=f"No synced lyrics on LRCLIB for {artist} – {title}.")
            return
        srt = work_dir / ".lyrics.srt"
        srt.write_text(lyrics.to_srt(found, info.get("duration")), encoding="utf-8")
        try:
            checks.embed_subtitles(path, srt, "Lyrics")
            emit("note", message=f"Lyrics added as subtitles (LRCLIB: {artist} – {title}).")
        except ValueError as exc:
            emit("note", message=f"No lyrics: {exc}")
        finally:
            srt.unlink(missing_ok=True)

    class Collect(PostProcessor):
        """Last step of each video: its lyrics when asked, its check, and its
        file, where it ended up. A file that fails its check is not kept."""

        def run(self, info):
            path = info.get("filepath")
            if not path or not os.path.isfile(path):
                return [], info
            path = Path(path)
            relative = os.path.relpath(path, work_dir)
            if options.lyrics:
                emit("stage", stage="processing", step="lyrics")
                add_lyrics(path, info)
            emit("stage", stage="processing", step="check")
            result = checks.check(path, info.get("duration"))
            if not result["ok"]:
                emit("error", message=f"{info.get('title') or relative}: the file failed its check: "
                                      f"{' '.join(result['problems'])}")
                path.unlink(missing_ok=True)
                return [], info
            made.append(relative)
            emit("file", path=relative, checks=result)
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
