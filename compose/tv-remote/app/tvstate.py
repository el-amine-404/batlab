"""A TV's state as a whole, and power, which waits until the TV has changed."""

from __future__ import annotations

import time

from playback import media
from samsung import app_in_front, audio, device_info, remote, wake
from store import load_tvs


def status(mac: str) -> dict:
    ip = load_tvs()[mac]["ip"]
    dev = device_info(ip, timeout=1.5)
    if not dev:
        return {"power": "off"}
    if dev["power"] != "on":
        return {"power": "standby"}
    app = app_in_front(mac)
    m = media(mac)
    if not app and m and m["source"] == "jellyfin":
        app = "Jellyfin"  # no app ID for the store version; its server knows what plays
    if m and m["source"] == "youtube":
        app = "YouTube"
    return {"power": "on", **audio(ip), "app": app, "media": m}


def wait_for(mac: str, want, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        st = status(mac)
        if want(st["power"]) or time.monotonic() > deadline:
            return st
        time.sleep(1)


def power(mac: str) -> dict:
    """Toggle, finishing the job: off sends POWER and waits for the screen to
    go; on wakes the network (Wake-on-LAN), then sends POWER from standby."""
    r = remote(mac)
    st = status(mac)
    if st["power"] == "on":
        r.run(lambda tv: tv.send_key("KEY_POWER"))
        return wait_for(mac, lambda p: p != "on", 12)
    if st["power"] == "off":
        wake(mac)
        st = wait_for(mac, lambda p: p != "off", 15)
        if st["power"] == "off":
            raise ConnectionError("the TV did not wake; turn on Power On with Mobile in its settings")
    if st["power"] == "standby":
        r.run(lambda tv: tv.send_key("KEY_POWER"))
        st = wait_for(mac, lambda p: p == "on", 12)
    return st
