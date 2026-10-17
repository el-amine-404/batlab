"""What every library holds, kept in memory and read again every EVERY
seconds (or at once after a library changes), so marking a list of videos
costs a few set lookups."""

import asyncio
import logging
import time
from contextlib import suppress

import httpx

from ..integrations import Integrations
from .base import Snapshot, song_key

log = logging.getLogger("clipseerr.libraries")
EVERY = 10 * 60


class LibraryIndex:
    def __init__(self, store: Integrations, http: httpx.AsyncClient):
        self._store = store
        self._http = http
        self._snapshot = Snapshot()
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
                found = await kind.read(self._http, item["config"])
            except Exception as exc:
                self._store.note(item["id"], False, f"Couldn't read it: {exc}")
                log.warning("library %s: %s", item["name"], exc)
                continue
            snapshot.merge(found)
            self._store.note(item["id"], True, f"{len(found.songs)} songs, {len(found.video_songs)} music videos")
        self._snapshot = snapshot
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
