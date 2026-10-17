"""What every library holds, kept in memory and read again every EVERY
seconds (or at once after a library changes), so marking a list of videos
costs a few set lookups."""

import asyncio
import base64
import hashlib
import logging
import time
from contextlib import suppress

import httpx

from ..integrations import Integrations
from .base import FileRef, Snapshot, song_key

log = logging.getLogger("clipseerr.libraries")
EVERY = 10 * 60


class LibraryIndex:
    def __init__(self, store: Integrations, http: httpx.AsyncClient):
        self._store = store
        self._http = http
        self._snapshot = Snapshot()
        self._tokens: dict[str, FileRef] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.read_at: float | None = None

    def held(self, youtube_id: str | None, title: str, artist: str) -> list[str]:
        """["audio", "video"], or a part of it: what the libraries have of this video."""
        key = song_key(title, artist)
        found = []
        if all(key) and key in self._snapshot.songs:
            found.append("audio")
        if (youtube_id and youtube_id in self._snapshot.videos) or (all(key) and key in self._snapshot.video_songs):
            found.append("video")
        return found

    def files(self, youtube_id: str | None, title: str, artist: str) -> list[FileRef]:
        """The files the libraries hold of this video: its own file first (by
        its YouTube id), then another upload of its song, then the song."""
        key = song_key(title, artist)
        found: list[FileRef] = []
        for lookup in ((("yt", youtube_id),) if youtube_id else ()) + ((("video", *key), ("song", *key))
                                                                      if all(key) else ()):
            for file in self._snapshot.files.get(lookup, []):
                if file not in found:
                    found.append(file)
        return found

    @staticmethod
    def token(file: FileRef) -> str:
        """The name a download link gives a file: opaque, and valid only while
        the index holds that file."""
        digest = hashlib.sha256(f"{file.library}\0{file.ref}".encode()).digest()[:12]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def by_token(self, token: str) -> FileRef | None:
        return self._tokens.get(token)

    async def open(self, file: FileRef):
        """(chunks, size) of a file the index holds, from its library."""
        item = next((i for i in self._store.items(enabled_only=True) if i["id"] == file.library), None)
        kind = self._store.types.get(item["type"]) if item else None
        if not kind:
            raise OSError("Its library is gone.")
        return await kind.open(self._http, item["config"], file)

    def mark(self, videos) -> None:
        """Sets each video's library (models with youtube_id, title, artist)."""
        for video in videos:
            video.library = self.held(video.youtube_id, video.title, video.artist)

    async def read(self) -> None:
        snapshot = Snapshot()
        for item in self._store.items(enabled_only=True):
            kind = self._store.types.get(item["type"])
            if not kind:
                continue
            try:
                found = await kind.read(self._http, item["config"], item["id"])
            except Exception as exc:
                self._store.note(item["id"], False, f"Couldn't read it: {exc}")
                log.warning("library %s: %s", item["name"], exc)
                continue
            snapshot.merge(found)
            self._store.note(item["id"], True, f"{len(found.songs)} songs, {len(found.video_songs)} music videos")
        self._snapshot = snapshot
        self._tokens = {self.token(file): file for files in snapshot.files.values() for file in files}
        self.read_at = time.time()

    def changed(self) -> None:
        self._wake.set()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="libraries")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self.read()
            except Exception:
                log.exception("reading the libraries failed")
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), EVERY)
