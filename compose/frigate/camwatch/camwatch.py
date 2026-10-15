"""Says when Frigate's cameras go dark, and why, and when they are back.

On 2026-10-14 the DVR moved from 192.168.1.101 to .2 (a static lease AdGuard
had just been given) while Frigate still dialed .101: both cameras were dark
for three and a half hours and nothing said so. Frigate restarts its own
ffmpeg every few seconds but tells no one.

Every CAMWATCH_INTERVAL seconds Frigate's stats are read; a camera giving no
frames (camera_fps 0) for CAMWATCH_AFTER seconds is announced once, with what
the DVR's address answers, then again every CAMWATCH_REMIND seconds while it
stays dark. Messages go through Apprise (compose/apprise) to the "cameras"
channel. Standard library and batlab_common (lib/batlab-common) only.
"""

import json
import os
import socket
import time
import urllib.request
from datetime import datetime

from batlab_common import notify as apprise

FRIGATE = os.environ.get("CAMWATCH_FRIGATE", "http://frigate:5000")
DVR_HOST = os.environ.get("FRIGATE_DVR_HOST", "")
NOTIFY_URL = os.environ.get("CAMWATCH_NOTIFY_URL", "")
NOTIFY_TAG = os.environ.get("CAMWATCH_NOTIFY_TAG", "cameras")
INTERVAL = int(os.environ.get("CAMWATCH_INTERVAL", "60"))
AFTER = int(os.environ.get("CAMWATCH_AFTER", "300"))
REMIND = int(os.environ.get("CAMWATCH_REMIND", "21600"))
HEARTBEAT = "/tmp/heartbeat"


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def names():
    """camera -> friendly name, from Frigate's config."""
    try:
        cameras = get(f"{FRIGATE}/api/config")["cameras"]
        return {k: v.get("friendly_name") or k for k, v in cameras.items()}
    except Exception:
        return {}


def dark():
    """The cameras giving no frames, or None when Frigate does not answer."""
    try:
        cameras = get(f"{FRIGATE}/api/stats")["cameras"]
    except Exception as error:
        log(f"frigate stats: {error}")
        return None
    return {name for name, stats in cameras.items() if not stats.get("camera_fps")}


def diagnose():
    """What the DVR's address says, in words."""
    if not DVR_HOST:
        return "FRIGATE_DVR_HOST is not set."
    try:
        address = socket.gethostbyname(DVR_HOST)
    except OSError:
        return (f"`{DVR_HOST}` does not resolve: AdGuard has lost the DVR's static lease, "
                "or DNS is down (docs/cameras.md).")
    where = DVR_HOST if address == DVR_HOST else f"`{DVR_HOST}` ({address})"
    try:
        socket.create_connection((address, 554), timeout=5).close()
    except OSError as error:
        return (f"The DVR does not answer at {where}: {error.strerror or error}. "
                "Powered off, unplugged, or on another address.")
    return (f"The DVR answers RTSP at {where}, but Frigate gets no frames: "
            "check the RTSP login (FRIGATE_RTSP_*) and the cameras on the DVR.")


def notify(title, kind, fields):
    """One message: the title, then a "**name**: value" line per field."""
    if not NOTIFY_URL:
        return
    body = "\n".join(f"**{name}**: {value[:1000] or '-'}" for name, value in fields)
    try:
        apprise.send(NOTIFY_URL, title, body, tag=NOTIFY_TAG, kind=kind)
    except apprise.NotSent as error:  # a lost message never stops the watch
        log(f"notification: {error}")


def duration(seconds):
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes / 60:.1f} h"


def main():
    since = {}      # camera (or "frigate") -> when it went dark
    announced = {}  # camera -> when it was last announced
    friendly = {}
    log(f"watching {FRIGATE}, DVR {DVR_HOST or '?'}; alert after {AFTER}s")
    while True:
        now = time.time()
        down = dark()
        keys = {"frigate"} if down is None else down
        for key in keys:
            since.setdefault(key, now)
        for key in list(since):
            if key in keys:
                continue
            if key in announced:
                label = "Frigate" if key == "frigate" else friendly.get(key, key)
                notify(f"🟢 {label} is back", "success", [("Was dark for", duration(now - since[key]))])
                log(f"{key} back after {duration(now - since[key])}")
            since.pop(key)
            announced.pop(key, None)
        due = [k for k in since if now - since[k] >= AFTER
               and (k not in announced or now - announced[k] >= REMIND)]
        if due:
            friendly = names() or friendly
            labels = ["Frigate (its API does not answer)" if k == "frigate" else friendly.get(k, k) for k in due]
            reminder = all(k in announced for k in due)
            title = ("🔴 Still dark: " if reminder else "🔴 Cameras dark: ") + ", ".join(labels)
            longest = max(now - since[k] for k in due)
            notify(title, "failure", [("For", duration(longest)), ("Why", diagnose()),
                                ("Fix", "docs/cameras.md, \"The DVR's address\"")])
            log(f"announced {due}")
            for key in due:
                announced[key] = now
        with open(HEARTBEAT, "w") as out:
            out.write(str(int(now)))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
