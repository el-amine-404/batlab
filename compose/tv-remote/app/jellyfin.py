"""What the Jellyfin app on the TV plays, through Jellyfin's own API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from config import JELLYFIN_API_KEY, JELLYFIN_URL


def jellyfin(path: str, method: str = "GET"):
    req = urllib.request.Request(
        JELLYFIN_URL + path, method=method,
        headers={"Authorization": f'MediaBrowser Token="{JELLYFIN_API_KEY}"'},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        body = r.read()
    return json.loads(body) if body else None


_jf_cache: tuple[float, dict | None] = (0.0, None)


def jellyfin_session() -> dict | None:
    """The Jellyfin app on the TV, as Jellyfin's server sees it."""
    global _jf_cache
    if not (JELLYFIN_URL and JELLYFIN_API_KEY):
        return None
    if time.monotonic() - _jf_cache[0] < 2:
        return _jf_cache[1]
    try:
        sessions = jellyfin("/Sessions?ActiveWithinSeconds=180") or []
    except (OSError, ValueError, urllib.error.URLError):
        sessions = []
    tv = next((x for x in sessions if x.get("Client") == "Jellyfin for Tizen"), None)
    if tv and not tv.get("NowPlayingItem"):
        # Only while it plays: the app keeps talking to its server from the
        # background, so recent activity still said "Now: Jellyfin" at Home.
        tv = None
    _jf_cache = (time.monotonic(), tv)
    return tv


def jellyfin_media(session: dict) -> dict | None:
    item = session.get("NowPlayingItem")
    if not item:
        return None
    ps = session.get("PlayState") or {}
    sub = ""
    if item.get("SeriesName"):
        sub = item["SeriesName"]
        if item.get("ParentIndexNumber") is not None and item.get("IndexNumber") is not None:
            sub += f" · S{item['ParentIndexNumber']}E{item['IndexNumber']}"
    elif item.get("ProductionYear"):
        sub = str(item["ProductionYear"])
    return {
        "source": "jellyfin", "id": item.get("Id"), "session": session.get("Id"),
        "title": item.get("Name") or "Jellyfin", "subtitle": sub,
        "state": "paused" if ps.get("IsPaused") else "playing",
        "position": (ps.get("PositionTicks") or 0) / 1e7,
        "duration": (item.get("RunTimeTicks") or 0) / 1e7,
        # The Tizen app takes no commands from the server, only the TV's media
        # keys: no seeking to a position, no previous/next.
        "remote": bool(session.get("SupportsRemoteControl")),
        "can_seek": bool(ps.get("CanSeek")) and bool(session.get("SupportsRemoteControl")),
        "can_skip": bool(session.get("SupportsRemoteControl")), "at": time.time(),
    }


def jellyfin_command(session_id: str, command: str, query: str = "") -> bool:
    try:
        jellyfin(f"/Sessions/{session_id}/Playing/{command}{query}", "POST")
        return True
    except (OSError, urllib.error.URLError):
        return False
