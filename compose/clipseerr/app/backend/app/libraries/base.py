"""A library: somewhere songs or music videos already are (Settings,
Libraries). clipseerr reads each one now and then (index.py) and marks what
it shows: a video whose song or whose file is there gets a green check, and
its page says so before anyone asks for it again.

A new kind of library is one module with one LibraryType subclass, listed in
libraries/__init__.py; its form is drawn from its fields.
"""

import re
from dataclasses import dataclass, field

import httpx
from batlab_common import titles

from ..integrations import IntegrationType

# A YouTube id in a file's name, as yt-dlp and MeTube write it: "Title [dQw4w9WgXcQ].mp4".
YOUTUBE_IN_NAME = re.compile(r"\[([\w-]{11})\]")
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def song_key(title: str, artist: str) -> tuple[str, str]:
    """One song whoever names it: its title without brackets, its first
    artist, both simple ("Gnarly (Clean Edit)", "KATSEYE & X" → ("gnarly", "katseye"))."""
    bare = _BRACKETS.sub("", title or "").strip() or (title or "")
    first = next(iter(titles.FEAT.split(artist or "")), artist or "")
    return titles.simple(bare), titles.simple(first)


@dataclass
class Snapshot:
    """What a library holds, as clipseerr matches it."""

    # Music video files, by their YouTube id, and by song_key() (a file from another upload of the song).
    videos: set[str] = field(default_factory=set)
    video_songs: set[tuple[str, str]] = field(default_factory=set)
    # Songs, by song_key().
    songs: set[tuple[str, str]] = field(default_factory=set)

    def add_song(self, title: str, artist: str) -> None:
        key = song_key(title, artist)
        if all(key):
            self.songs.add(key)

    def add_video(self, title: str, artist: str, youtube_id: str | None = None) -> None:
        if youtube_id:
            self.videos.add(youtube_id)
        key = song_key(title, artist)
        if all(key):
            self.video_songs.add(key)

    def merge(self, other: "Snapshot") -> None:
        self.videos |= other.videos
        self.video_songs |= other.video_songs
        self.songs |= other.songs


class LibraryType(IntegrationType):
    async def read(self, http: httpx.AsyncClient, config: dict) -> Snapshot:
        """Everything it holds; raises with a sentence when it can't."""
        raise NotImplementedError

    async def test(self, http: httpx.AsyncClient, config: dict) -> str:
        found = await self.read(http, config)
        parts = [f"{len(found.songs)} songs" if found.songs else "", f"{len(found.video_songs)} music videos"
                 if found.video_songs else ""]
        return "Read: " + (", ".join(p for p in parts if p) or "nothing yet") + "."
