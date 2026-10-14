"""Downloads any link yt-dlp supports, audio only, with its tags and cover
(run with songdrop's interpreter, which carries the pinned yt-dlp).

stdin: {"url", "job_dir", "settings", "max_items", "deno", "cookies"}; events:
see protocol.py.

"One song per chapter" splits a video (a whole album or a mix uploaded as one)
at its chapters; each part is reported as its own song, tagged with the
chapter's title (and its artist, when the chapter reads "Artist - Song"), the
video as its album and the chapter's number, and the whole video is dropped.
"""

import os
import re
import time

import mutagen
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError, download_range_func

from protocol import Events, read_config

_ID_IN_MESSAGE = re.compile(r"\[[^\]]+\]\s*([\w-]+):")
# "01. ", "1 - ", "[03:15] ", "12) " before a chapter's title.
_CHAPTER_PREFIX = re.compile(r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*[-–|]?\s*|\d{1,3}\s*[.)\-–:]\s+)")


# "[Full BeatTape]", "(Full Album)", "| Full EP" after an album's name.
_FULL = re.compile(r"\s*(?:[\[(][^\])]*\b(?:full|album|tape|ep|mix|tracklist)\b[^\])]*[\])]|\|.*$)", re.I)


def chapter_tags(chapter_title: str, number: int, total: int, info: dict) -> dict:
    """The tags of one chapter's file."""
    title = _CHAPTER_PREFIX.sub("", chapter_title or "").strip() or f"Part {number}"
    video = info.get("title") or ""
    album = info.get("album") or ""
    artist = info.get("artist") or info.get("creator") or ""
    if not artist and " - " in video:
        # "97SPECIAL - Escape [Full BeatTape]": the uploader is often a channel
        # that collects other people's music.
        artist, named = (part.strip() for part in video.split(" - ", 1))
        album = album or named
    artist = artist or (info.get("uploader") or "").removesuffix(" - Topic")
    album = _FULL.sub("", album or video).strip()
    if " - " in title:
        artist, title = (part.strip() for part in title.split(" - ", 1))
    tags = {"title": title, "tracknumber": f"{number}/{total}", "album": album}
    if artist:
        tags["artist"] = artist
    return {key: value for key, value in tags.items() if value}


def copy_cover(source: str, target: str) -> None:
    """The whole video's embedded cover onto one chapter's file. ffmpeg
    cannot copy it while splitting (an Ogg file's picture is not a stream it
    can write back), so the split runs before the cover is embedded."""
    try:
        whole, part = mutagen.File(source), mutagen.File(target)
        if whole is None or part is None or whole.tags is None:
            return
        if part.tags is None:
            part.add_tags()
        for key in ("metadata_block_picture", "covr"):  # Ogg (Opus, Vorbis), MP4
            if key in whole.tags:
                part.tags[key] = whole.tags[key]
        for frame in whole.tags.getall("APIC") if hasattr(whole.tags, "getall") else []:  # MP3
            part.tags.add(frame)
        part.save()
    except Exception as exc:
        print(f"WARNING: no cover for {target}: {exc}", flush=True)


def retag(path: str, tags: dict) -> None:
    """Best effort: musicguard names the song anyway (Shazam)."""
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return
        for key, value in tags.items():
            audio[key] = value
        audio.save()
    except Exception as exc:
        print(f"WARNING: could not tag {path}: {exc}", flush=True)


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
    """Last step for each item: report the finished file, or its chapters."""

    def __init__(self, events: Events, split: bool):
        super().__init__(None)
        self.events = events
        self.split = split
        self.parts = 0  # chapter files reported so far, across a playlist

    def run(self, info):
        path = info.get("filepath")
        parts = [c for c in (info.get("chapters") or []) if c.get("filepath") and os.path.isfile(c["filepath"])]
        if self.split and parts:
            for number, chapter in enumerate(parts, 1):
                tags = chapter_tags(chapter.get("title") or "", number, len(parts), info)
                if path and os.path.isfile(path):
                    copy_cover(path, chapter["filepath"])
                retag(chapter["filepath"], tags)
                self.events.emit("file", index=self.parts, path=chapter["filepath"],
                                 title=tags["title"], artist=tags.get("artist"))
                self.parts += 1
            if path and os.path.isfile(path):
                os.unlink(path)  # the whole video: its chapters are the songs
            return [], info
        if self.split and path and os.path.isfile(path):
            self.events.emit("note", index=self.parts, name=info.get("title") or "",
                             message="This video has no chapters, so it was kept as one song.")
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
    if settings.get("split_chapters"):
        # After the tags, before the cover: ffmpeg cannot copy an embedded
        # cover into the parts; _Collect copies it from the whole file.
        processors.append({"key": "FFmpegSplitChapters", "force_keyframes": False})
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
        "playlistend": min(config["max_items"], settings.get("playlist_limit") or config["max_items"]),
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "cachedir": os.path.join(job_dir, ".cache"),
    }
    if config.get("deno"):
        base["js_runtimes"] = {"deno": {"path": config["deno"]}}
    if config.get("cookies"):
        base["cookiefile"] = config["cookies"]

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
        chapters = len(info.get("chapters") or []) if settings.get("split_chapters") else 0
        events.emit("meta", title=f"{artist} - {title}" if artist else title, total=max(1, chapters))

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
        "outtmpl": {"default": "%(title).150B [%(id)s].%(ext)s",
                    "chapter": "%(title).80B - %(section_number)03d %(section_title).80B [%(id)s].%(ext)s"},
        "windowsfilenames": True,
        "writethumbnail": settings["embed_cover"],
        "ignoreerrors": "only_download",
        "postprocessors": _postprocessors(settings),
        "progress_hooks": [progress],
    }
    if settings.get("clip"):
        start, end = settings["clip"]
        options["download_ranges"] = download_range_func(None, [(start, end if end is not None else float("inf"))])
        options["force_keyframes_at_cuts"] = True
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(_Collect(events, bool(settings.get("split_chapters"))), when="after_move")
            ydl.download([config["url"]])
    except DownloadError as exc:
        events.fatal(str(exc))


if __name__ == "__main__":
    main()
