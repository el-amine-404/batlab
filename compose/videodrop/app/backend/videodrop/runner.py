"""One download, in a process of its own (python -m videodrop.runner): the
server stays responsive whatever yt-dlp and ffmpeg do, a download is stopped
by stopping its process (and its ffmpeg with it), and a crash ends only it.

stdin: one line, a JSON object {"url", "work_dir", "options", "output",
"section", "rate_limit", "max_filesize", "cookies", "deno", "song"}; later, one more line
answering the fingerprint event: {"reuse": <an earlier job's id> or null}.
stdout: events, one JSON object a line (EVENTS); anything else the process
prints goes to stderr, which the server keeps in the job's log. Exit 0 when at least one file was
made or an earlier one reused, 1 otherwise.

A video (not a playlist) is read first without downloading: its fingerprint
(ytdlp.fingerprint, what the site serves for these options) goes to the
server, which answers whether an earlier download got that same file; then
nothing is downloaded.

Each step after the download (joining, tags, cover…) writes a new copy of
the file; how far that copy has grown, against the file it is made from, is
the step's progress (StepWatch).

A finished file is renamed to a name any disk and player takes
(options.safe_path) before it is checked."""

import copy
import json
import os
import sys
import threading
import time
from pathlib import Path

# Events: {"event": name, ...}.
#   item      {index, total, id, title, uploader, thumbnail, duration}  a video starts
#   progress  {index, downloaded, total, speed, eta, fragment, fragments} at most twice a second; bytes of
#                                                                        the video's streams together
#   stage     {stage, step}                                              downloading, processing (step: which
#                                                                        part of it: Merger, EmbedThumbnail…)
#   step      {percent}                                                  how far the step's new copy has grown
#   fingerprint {value}                                                  the server answers on stdin (reuse)
#   reused    {job}                                                      an earlier job's file is the same: done
#   file      {path, checks}                                             a finished file, relative to work_dir, checked
#   note      {message}                                                  beside the download: lyrics found or not
#   log       {level, message}                                           what yt-dlp says as it goes (the job's log)
#   warning   {message}
#   error     {message}                                                  a video failed (the job may go on)
EVENTS = ("item", "progress", "stage", "step", "fingerprint", "reused", "file", "note", "log", "warning", "error")
PROGRESS_EVERY = 0.5
STEP_EVERY = 1.0
# Files that aren't the video while it is worked on.
NOT_THE_VIDEO = {".jpg", ".jpeg", ".png", ".webp", ".srt", ".vtt", ".ass", ".json", ".part", ".ytdl", ".lrc"}


class Emitter:
    """Events to stdout, one line each; the step watcher writes from its own thread."""

    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()

    def __call__(self, event: str, **fields) -> None:
        line = json.dumps({"event": event, **fields}, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self._stream.write(line)
            self._stream.flush()


def _media_sizes(folder: Path) -> dict[str, int]:
    sizes = {}
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() not in NOT_THE_VIDEO and not path.name.startswith(".cookies"):
            try:
                sizes[str(path)] = path.stat().st_size
            except OSError:
                pass
    return sizes


class StepWatch:
    """While a step runs: the files it started from, and how far the new
    file it writes has grown against them (a percent, emitted every
    STEP_EVERY). A step that changes the file in place has none to show."""

    def __init__(self, emit, folder: Path):
        self._emit = emit
        self._folder = folder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.stop()
        before = _media_sizes(self._folder)
        if not before:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, args=(before, self._stop), daemon=True)
        self._thread.start()

    def _watch(self, before: dict[str, int], stop: threading.Event) -> None:
        # Joining picture and sound: the new file is both together; else it is a copy of the biggest one.
        sources = sorted(before.values(), reverse=True)
        whole = sum(sources[:2]) if len(sources) > 1 else sources[0]
        last = -1
        while not stop.wait(STEP_EVERY):
            now = _media_sizes(self._folder)
            grown = sum(size for path, size in now.items() if path not in before)
            if whole and grown:
                percent = min(99, round(100 * grown / whole))
                if percent != last:
                    last = percent
                    self._emit("step", percent=percent)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


def _stdout_for_events():
    """The real stdout, for events only; fd 1 then points at stderr, so that
    nothing else (yt-dlp, ffmpeg, a library's print) can garble them."""
    events = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return events


def main() -> int:
    emit = Emitter(_stdout_for_events())
    config = json.loads(sys.stdin.readline())

    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError

    from . import checks, lyrics
    from .options import VideoOptions, safe_path
    from .ytdlp import fingerprint, params

    work_dir = Path(config["work_dir"])
    options = VideoOptions.model_validate(config["options"])
    section = config.get("section")
    made: list[str] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    last = [0.0]

    class Logger:
        """yt-dlp's lines, for the job's log (its own [debug] lines only when asked for: they are many)."""

        def debug(self, message):
            if message.startswith("[debug] "):
                return
            emit("log", level="info", message=message[:2000])

        def info(self, message):
            emit("log", level="info", message=message[:2000])

        def warning(self, message):
            emit("warning", message=message[:2000])

        def error(self, message):
            errors.append(message)
            emit("error", message=message[:2000])

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

    watch = StepWatch(emit, work_dir)

    def postprocessing(status: dict) -> None:
        if status.get("status") == "started":
            emit("stage", stage="processing", step=status.get("postprocessor"))
            watch.start()
        else:
            watch.stop()
            if not parts:
                # A step before the download (the cover's): the download comes next, and a section's (by
                # ffmpeg) says nothing until it ends.
                emit("stage", stage="downloading")

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

    class CollectPP(PostProcessor):
        """Last step of each video: its lyrics when asked, its check, and its
        file, where it ended up. A file that fails its check is not kept."""

        def run(self, info):
            path = info.get("filepath")
            if not path or not os.path.isfile(path):
                return [], info
            path = Path(path)
            safe = work_dir / safe_path(os.path.relpath(path, work_dir))
            if safe != path and not safe.exists():
                safe.parent.mkdir(parents=True, exist_ok=True)
                path = path.rename(safe)
            relative = os.path.relpath(path, work_dir)
            if options.lyrics:
                emit("stage", stage="processing", step="lyrics")
                add_lyrics(path, info)
            emit("stage", stage="processing", step="check")
            expected = info.get("duration")
            if section and expected:
                expected = max(0.0, min(section[1], expected) - section[0])
            result = checks.check(path, expected)
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
    if section:
        from yt_dlp.utils import download_range_func

        # Cut at the keyframes around it, by ffmpeg, without re-encoding.
        ydl_params.update(download_ranges=download_range_func(None, [tuple(section)]), force_keyframes_at_cuts=False)
    # Until a byte comes (or a step), it is starting: the site is being read.
    try:
        with yt_dlp.YoutubeDL(ydl_params) as ydl:
            ydl.add_post_processor(CollectPP(), when="after_move")
            found = ydl.extract_info(config["url"], download=False, process=False)
            if found.get("_type") in ("playlist", "multi_video"):
                ydl.download([config["url"]])
            else:
                info = ydl.process_ie_result(copy.deepcopy(found), download=False)
                item(info)
                emit("fingerprint", value=fingerprint(info))
                answer = json.loads(sys.stdin.readline() or "{}")
                if answer.get("reuse"):
                    emit("reused", job=answer["reuse"])
                    return 0
                ydl.process_ie_result(found, download=True)
    except DownloadError as exc:
        if not errors:
            emit("error", message=str(exc)[:1000])
    except Exception as exc:  # yt-dlp's own bug: said, not hidden
        emit("error", message=f"yt-dlp failed: {exc.__class__.__name__}: {exc}"[:1000])
    finally:
        watch.stop()
    if not made and not errors:
        emit("error", message="Nothing was downloaded: the file is probably over the size limit, or the link "
                              "has no video.")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
