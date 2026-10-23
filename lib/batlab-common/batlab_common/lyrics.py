"""Lyrics from stanzarr (GET /api/v1/lyrics), for the apps that show or
embed them (clipseerr, videodrop). Optional: an app without STANZARR_URL
never asks, and one whose stanzarr is down falls back to its own LRCLIB
question at once.

Down means: no answer within TIMEOUT, or an error. After one, stanzarr is
left alone for PAUSE seconds (a breaker), so a stopped stanzarr costs one
slow question, not one per song. Standard library only: callers in async
code run it in a thread."""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT = 6.0
PAUSE = 300.0


class Stanzarr:
    def __init__(self, url: str, key: str, app: str, timeout: float = TIMEOUT, pause: float = PAUSE):
        self.url = url.rstrip("/")
        self.key = key
        self.app = app
        self.timeout = timeout
        self.pause = pause
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def on(self) -> bool:
        return bool(self.url and self.key)

    def available(self) -> bool:
        with self._lock:
            return self.on and time.monotonic() >= self._down_until

    def _down(self, why: object) -> None:
        with self._lock:
            self._down_until = time.monotonic() + self.pause
        log.info("stanzarr unavailable (%s): own lyrics for %d s", why, int(self.pause))

    def lyrics(self, artist: str, title: str, album: str = "", duration: float | None = None) -> dict | None:
        """stanzarr's answer ({"found", "synced", "lrc", "text", "instrumental",
        "from", "source", "origin"}), or None when it can't be asked: then the
        caller asks its own source. An answer of found: false is an answer."""
        if not self.available() or not artist or not title:
            return None
        params = {"artist": artist[:200], "title": title[:200]}
        if album:
            params["album"] = album[:200]
        if duration:
            params["duration"] = f"{float(duration):.0f}"
        request = urllib.request.Request(f"{self.url}/api/v1/lyrics?{urllib.parse.urlencode(params)}", headers={
            "X-Api-Key": self.key, "Accept": "application/json", "User-Agent": f"{self.app} (batlab)"})
        # Straight to stanzarr, never through an HTTP proxy the process may have.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                answer = json.loads(response.read(2 * 1024 * 1024))
        except (OSError, ValueError) as error:
            self._down(error)
            return None
        return answer if isinstance(answer, dict) and "found" in answer else None
