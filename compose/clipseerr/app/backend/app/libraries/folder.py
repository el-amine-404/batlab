"""A folder of files, read where clipseerr sees it (a volume in its compose
file): music videos or songs, named "Artist - Title" or "Artist/Title"; a
YouTube id in the name ("… [id].mp4", as MeTube saves them) matches exactly."""

import asyncio
import os
from pathlib import Path

from ..integrations import Choice, Field, Invalid
from .base import YOUTUBE_IN_NAME, LibraryType, Snapshot

MEDIA = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mp3", ".flac", ".m4a", ".opus", ".ogg", ".wav", ".aac"}


def scan(root: Path, holds: str) -> Snapshot:
    found = Snapshot()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() not in MEDIA:
                continue
            youtube = YOUTUBE_IN_NAME.search(stem)
            text = YOUTUBE_IN_NAME.sub("", stem).strip()
            artist, sep, title = text.partition(" - ")
            if not sep:
                # "Artist/Title.mp4": the folder names the artist.
                artist, title = Path(folder).name, text
            if holds == "videos":
                found.add_video(title, artist, youtube.group(1) if youtube else None)
            else:
                found.add_song(title, artist)
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

    async def read(self, http, config: dict) -> Snapshot:
        root = Path(config["path"])
        if not root.is_dir():
            raise OSError(f"clipseerr can't see {root}: mount it in its compose file.")
        return await asyncio.to_thread(scan, root, config.get("holds", "videos"))
