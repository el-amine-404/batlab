"""Turn a TV off later. Runs here, so it works with the phone locked, and is kept
in DATA_DIR, so a restart does not lose it."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from config import DATA_DIR

log = logging.getLogger("tv-remote")

MAX_MINUTES = 360
CHECK_EVERY = 5
_FILE: Path = DATA_DIR / "sleep.json"
_lock = threading.Lock()


def _load() -> dict[str, float]:
    try:
        return json.loads(_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(timers: dict[str, float]) -> None:
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(timers))
    tmp.replace(_FILE)


def set_timer(mac: str, minutes: int) -> float:
    at = time.time() + min(MAX_MINUTES, max(1, minutes)) * 60
    with _lock:
        timers = _load()
        timers[mac] = at
        _save(timers)
    return at


def cancel(mac: str) -> bool:
    with _lock:
        timers = _load()
        found = timers.pop(mac, None) is not None
        if found:
            _save(timers)
    return found


def when(mac: str) -> float | None:
    with _lock:
        return _load().get(mac)


def due(now: float | None = None) -> list[str]:
    """The TVs whose time has come; each is returned once."""
    now = now or time.time()
    with _lock:
        timers = _load()
        ready = [mac for mac, at in timers.items() if at <= now]
        if ready:
            _save({m: a for m, a in timers.items() if m not in ready})
    return ready


def start(turn_off: Callable[[str], None]) -> None:
    def run() -> None:
        while True:
            time.sleep(CHECK_EVERY)
            for mac in due():
                try:
                    turn_off(mac)
                    log.info("%s: sleep timer turned the TV off", mac)
                except Exception as err:  # noqa: BLE001 - one TV must not stop the others
                    log.warning("%s: sleep timer could not turn the TV off: %s", mac, err)
    threading.Thread(target=run, daemon=True, name="sleeptimer").start()
