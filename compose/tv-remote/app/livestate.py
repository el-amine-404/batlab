"""One reader per TV, shared by every open page. A page used to ask every few
seconds, and each answer took about ten calls to the TV; now one thread reads
while someone is watching and pages are told only what changed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("tv-remote")

MEDIA_EVERY = 1.0
STATUS_EVERY = 4.0
FRESH_ENOUGH = 2.0  # seconds a page's own request may be answered from memory
JUMP = 3.0          # seconds of playback position off course that count as a seek


def _differs(old: dict | None, new: dict) -> bool:
    """A playing video's position changes every second, and the page counts it
    by itself: only a jump is news."""
    if old is None:
        return True
    plain = lambda st: {**st, "media": _without_clock(st.get("media"))}  # noqa: E731
    if plain(old) != plain(new):
        return True
    was, now = old.get("media"), new.get("media")
    if not was or not now or "position" not in now or "position" not in was:
        return False
    expected = was["position"]
    if was.get("state") == "playing":
        expected += now.get("at", 0) - was.get("at", 0)
    return abs(now["position"] - expected) > JUMP


def _without_clock(media: dict | None) -> dict | None:
    return {k: v for k, v in media.items() if k not in ("position", "at")} if media else None


class LiveState:
    def __init__(self, tv_factory: Callable[[], object]) -> None:
        self._tv_factory = tv_factory
        self._changed = threading.Condition()
        self._read_lock = threading.Lock()
        self._wake = threading.Event()
        self.state: dict | None = None
        self.version = 0
        self._read_at = 0.0
        self._watchers = 0

    def _publish(self, new: dict) -> None:
        with self._changed:
            if _differs(self.state, new):
                self.version += 1
                self.state = new
                self._changed.notify_all()
            elif self.state and self.state.get("media") and new.get("media"):
                self.state = {**self.state, "media": new["media"]}  # fresh clock, no news

    def read(self) -> dict:
        with self._read_lock:
            new = self._tv_factory().status()
            self._read_at = time.monotonic()
        self._publish(new)
        return new

    def current(self) -> dict:
        """The state, read now unless it is recent. Pages not streaming use this."""
        if self.state is not None and time.monotonic() - self._read_at < FRESH_ENOUGH:
            return self.state
        return self.read()

    def poke(self) -> None:
        """Something was just done to the TV: the next answer must be a new one."""
        self._read_at = 0.0
        self._wake.set()

    def watch(self) -> None:
        with self._changed:
            self._watchers += 1
            start = self._watchers == 1
        if start:
            threading.Thread(target=self._run, daemon=True, name="livestate").start()

    def unwatch(self) -> None:
        with self._changed:
            self._watchers -= 1

    def wait(self, seen: int, timeout: float) -> tuple[int, dict | None]:
        with self._changed:
            self._changed.wait_for(lambda: self.version != seen, timeout)
            return self.version, self.state

    def _run(self) -> None:
        last_full = 0.0
        while self._watchers > 0:
            try:
                now = time.monotonic()
                off = not self.state or self.state.get("power") != "on"
                if off or self._read_at == 0.0 or now - last_full >= STATUS_EVERY:
                    self.read()
                    last_full = time.monotonic()
                else:
                    self._publish({**self.state, "media": self._tv_factory().media()})
            except Exception as err:  # noqa: BLE001 - a page must not lose its reader over one bad read
                log.info("status read failed: %s", err)
            self._wake.wait(STATUS_EVERY if off else MEDIA_EVERY)
            self._wake.clear()


_lives: dict[str, LiveState] = {}
_lock = threading.Lock()


def live(mac: str, tv_factory: Callable[[], object]) -> LiveState:
    with _lock:
        if mac not in _lives:
            _lives[mac] = LiveState(tv_factory)
        return _lives[mac]
