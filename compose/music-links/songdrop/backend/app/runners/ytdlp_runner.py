"""Downloads any link yt-dlp supports, audio only, with its tags and cover
(run with songdrop's interpreter, which carries the pinned yt-dlp).

stdin: {"url", "job_dir", "settings", "max_items", "deno"}; events: see protocol.py.
"""

import os
import re
import time

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

from protocol import Events, read_config

_ID_IN_MESSAGE = re.compile(r"\[[^\]]+\]\s*([\w-]+):")


class _Logger:
    """yt-dlp's messages: errors become item_error events, the rest is dropped
    (it is printed to stderr for the container log)."""

    def __init__(self, events: Events, names: dict):
        self.events = events
        self.names = names
        self.errors = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        print(f"WARNING: {message}", flush=True)

    def error(self, message: str) -> None:
        print(message, flush=True)
        self.errors.append(message)
        match = _ID_IN_MESSAGE.search(message)
        ident = match.group(1) if match else None
        index, name = self.names.get(ident, (None, ident or "?"))
        self.events.emit("item_error", index=index, name=name, message=message)


class _Collect(PostProcessor):
    """Last step for each item: report the finished file."""

    def __init__(self, events: Events):
        super().__init__(None)
        self.events = events

    def run(self, info):
        path = info.get("filepath")
        if path and os.path.isfile(path):
            self.events.emit(
                "file",
                index=(info.get("playlist_index") or 1) - 1,
                path=path,
                title=info.get("track") or info.get("title"),
                artist=info.get("artist") or info.get("creator") or info.get("uploader"),
            )
        return [], info


def _postprocessors(settings: dict) -> list:
    """In the order yt-dlp's own command line adds them."""
    processors = []
    if settings["sponsor_block"]:
        processors.append({"key": "SponsorBlock", "categories": ["music_offtopic"], "when": "after_filter"})
    extract = {"key": "FFmpegExtractAudio", "preferredcodec": settings["codec"], "nopostoverwrites": False}
    if settings["quality"] is not None:
        extract["preferredquality"] = settings["quality"]
    processors.append(extract)
    if settings["sponsor_block"]:
        processors.append({"key": "ModifyChapters", "remove_sponsor_segments": ["music_offtopic"]})
    processors.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": False})
    if settings["embed_cover"]:
        processors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        processors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return processors


def main() -> None:
    config = read_config()
    events = Events.from_env()
    settings = config["settings"]
    job_dir = config["job_dir"]
    names = {}  # video id -> (index, name), for error messages
    logger = _Logger(events, names)

    base = {
        "logger": logger,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": not settings["playlist"],
        "playlistend": config["max_items"],
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "cachedir": os.path.join(job_dir, ".cache"),
    }
    if config.get("deno"):
        base["js_runtimes"] = {"deno": {"path": config["deno"]}}

    try:
        with yt_dlp.YoutubeDL({**base, "extract_flat": "in_playlist"}) as ydl:
            info = ydl.extract_info(config["url"], download=False)
    except DownloadError as exc:
        events.fatal(str(exc))
    if not info:
        events.fatal(logger.errors[-1] if logger.errors else "Nothing to download at this link.")

    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            events.fatal("This playlist is empty, or none of it can be downloaded.")
        for index, entry in enumerate(entries):
            names[entry.get("id")] = (index, entry.get("title") or entry.get("id"))
        events.emit("meta", title=info.get("title") or "Playlist", total=len(entries))
    else:
        title = info.get("track") or info.get("title") or config["url"]
        artist = info.get("artist") or info.get("creator")
        names[info.get("id")] = (0, title)
        events.emit("meta", title=f"{artist} - {title}" if artist else title, total=1)

    last_report = {}

    def progress(status: dict) -> None:
        item = status.get("info_dict") or {}
        index = (item.get("playlist_index") or 1) - 1
        if status.get("status") == "downloading":
            if index not in last_report:
                events.emit("item_start", index=index, name=item.get("title") or item.get("id"))
                last_report[index] = 0.0
            now = time.monotonic()
            if now - last_report[index] < 1:
                return
            last_report[index] = now
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if total:
                events.emit("progress", index=index, percent=round(100 * status.get("downloaded_bytes", 0) / total, 1))

    options = {
        **base,
        "format": "bestaudio/best",
        "paths": {"home": job_dir, "temp": os.path.join(job_dir, ".tmp")},
        "outtmpl": {"default": "%(title).150B [%(id)s].%(ext)s"},
        "windowsfilenames": True,
        "writethumbnail": settings["embed_cover"],
        "ignoreerrors": "only_download",
        "postprocessors": _postprocessors(settings),
        "progress_hooks": [progress],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(_Collect(events), when="after_move")
            ydl.download([config["url"]])
    except DownloadError as exc:
        events.fatal(str(exc))


if __name__ == "__main__":
    main()
