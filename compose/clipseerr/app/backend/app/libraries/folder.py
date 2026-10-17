"""A folder of files, read where clipseerr sees it (a volume in its compose
file): music videos or songs, named "Artist - Title" or "Artist/Title"; a
YouTube id in the name ("… [id].mp4", as MeTube saves them) matches exactly."""

import asyncio
import os
from pathlib import Path

from ..integrations import Choice, Field, Invalid
from .base import YOUTUBE_IN_NAME, FileRef, LibraryType, Snapshot

MEDIA = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mp3", ".flac", ".m4a", ".opus", ".ogg", ".wav", ".aac"}


def scan(root: Path, holds: str, library: str = "") -> Snapshot:
    found = Snapshot()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() not in MEDIA:
                continue
            path = Path(folder) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            file = FileRef(library=library, kind="video" if holds == "videos" else "audio",
                           ref=str(path.relative_to(root)), name=name, size=size)
            youtube = YOUTUBE_IN_NAME.search(stem)
            text = YOUTUBE_IN_NAME.sub("", stem).strip()
            artist, sep, title = text.partition(" - ")
            if not sep:
                # "Artist/Title.mp4": the folder names the artist.
                artist, title = Path(folder).name, text
            if holds == "videos":
                found.add_video(title, artist, youtube.group(1) if youtube else None, file)
            else:
                found.add_song(title, artist, file)
    return found


class Folder(LibraryType):
    key = "folder"
    label = "Folder"
    description = ("Files in a folder clipseerr can read (mounted in its compose file), named \"Artist - Title\" "
                   "or \"Artist/Title\": music videos (a YouTube id in the name, as MeTube saves them, matches "
                   "exactly) or songs.")
    fields = (
        Field("path", "Folder", "text", help="Where clipseerr sees it, inside its container.",
              placeholder="/library/music-videos"),
        Field("holds", "It holds", "select", default="videos",
              choices=(Choice("videos", "Music videos"), Choice("songs", "Songs"))),
    )

    def validate(self, config: dict) -> None:
        if not config["path"].startswith("/"):
            raise Invalid("Folder must be a full path, starting with /.")

    def summary(self, config: dict) -> str:
        return config.get("path") or self.label

    async def read(self, http, config: dict, library: str = "") -> Snapshot:
        root = Path(config["path"])
        if not root.is_dir():
            raise OSError(f"clipseerr can't see {root}: mount it in its compose file.")
        return await asyncio.to_thread(scan, root, config.get("holds", "videos"), library)

    async def open(self, http, config: dict, file: FileRef):
        root = Path(config["path"]).resolve()
        path = (root / file.ref).resolve()
        # Only what the scan found under the folder, never a path that climbs out of it.
        if not path.is_relative_to(root) or not path.is_file():
            raise OSError(f"{file.name} is no longer in {root}.")

        async def chunks():
            with path.open("rb") as handle:
                while block := await asyncio.to_thread(handle.read, 1 << 20):
                    yield block

        return chunks(), path.stat().st_size
