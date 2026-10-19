"""Runs probe.py for the API (POST /api/v1/probe): at most AT_ONCE at a time
(a person typing must not become a burst of requests to YouTube), each
answer kept CACHE_FOR seconds, a probe stopped after TIMEOUT."""

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

from . import cookies, links
from .domain import Invalid
from .options import VideoOptions

AT_ONCE = 2
CACHE_FOR = 600
CACHE_SIZE = 256
TIMEOUT = 45


class ProbeFailed(Invalid):
    """The message is shown as is."""


class Prober:
    def __init__(self, state_dir: Path, deno: str | None, command: tuple[str, ...], proxy: str | None = None):
        self._state_dir = state_dir
        self._proxy = proxy
        self._deno = deno
        self._command = command
        self._slots: asyncio.Semaphore | None = None
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    async def probe(self, url: str, options: VideoOptions, output: str) -> dict:
        key = json.dumps([links.canonical(url), options.model_dump(), output], sort_keys=True)
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < CACHE_FOR:
            self._cache.move_to_end(key)
            return cached[1]
        self._slots = self._slots or asyncio.Semaphore(AT_ONCE)
        async with self._slots:
            # The cookies' own copy: yt-dlp writes them back.
            folder = Path(tempfile.mkdtemp(prefix="probe-"))
            try:
                info = await self._run({"url": url, "options": options.model_dump(), "output": output,
                                        "cookies": cookies.copy_for(self._state_dir, folder), "deno": self._deno,
                                        "proxy": self._proxy})
            finally:
                shutil.rmtree(folder, ignore_errors=True)
        self._cache[key] = (time.time(), info)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return info

    async def _run(self, request: dict) -> dict:
        process = await asyncio.create_subprocess_exec(
            *self._command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env={**os.environ, "HOME": "/tmp", "DENO_DIR": "/tmp/deno"},
            start_new_session=True)
        try:
            out, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ProbeFailed(f"The site didn't answer within {TIMEOUT} s.") from None
        try:
            answer = json.loads(out or b"{}")
        except ValueError:
            raise ProbeFailed("yt-dlp gave no answer.") from None
        if not answer.get("ok"):
            raise ProbeFailed(answer.get("message") or "yt-dlp couldn't read this link.")
        return answer["info"]
