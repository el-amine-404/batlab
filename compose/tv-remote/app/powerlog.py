"""Since when a TV has been on or off. The TV does not know, so it is watched
here all the time, whether or not a page is open, and kept in DATA_DIR."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from config import DATA_DIR

log = logging.getLogger("tv-remote")

WATCH_EVERY = 30
OFF_AFTER = 2  # readings: one lost answer on Wi-Fi is not a TV turned off
_FILE: Path = DATA_DIR / "power.json"
_lock = threading.Lock()
_first_off: dict[str, tuple[int, float]] = {}  # mac: (readings so far, time of the first)


def _load() -> dict[str, dict]:
    try:
        return json.loads(_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(log_: dict[str, dict]) -> None:
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(log_))
    tmp.replace(_FILE)


def observe(mac: str, power: str, now: float | None = None) -> None:
    """Note a reading: "on", or anything else for off."""
    now = now or time.time()
    is_on = power == "on"
    with _lock:
        log_ = _load()
        known = log_.get(mac)
        if is_on:
            _first_off.pop(mac, None)
        elif known and known["on"]:
            count, first = _first_off.get(mac, (0, now))
            _first_off[mac] = (count + 1, first)
            if count + 1 < OFF_AFTER:
                return
            now = first
        if not known or known["on"] != is_on:
            log_[mac] = {"on": is_on, "since": now}
            _save(log_)
            _first_off.pop(mac, None)


def since(mac: str) -> float | None:
    with _lock:
        known = _load().get(mac)
    return known["since"] if known else None


def start(macs: Callable[[], Iterable[str]], read: Callable[[str], str]) -> None:
    def run() -> None:
        while True:
            for mac in macs():
                try:
                    observe(mac, read(mac))
                except Exception as err:  # noqa: BLE001 - keep watching
                    log.info("%s: power watch: %s", mac, err)
            time.sleep(WATCH_EVERY)
    threading.Thread(target=run, daemon=True, name="powerlog").start()
