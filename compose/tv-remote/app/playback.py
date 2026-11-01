"""What plays on a TV and how to control it, whichever app it is in."""

from __future__ import annotations

from jellyfin import jellyfin_command, jellyfin_media, jellyfin_session
from samsung import remote
from youtube import youtube_link


def media(mac: str) -> dict | None:
    m = youtube_link(mac).snapshot()
    if m:
        return m
    s = jellyfin_session()
    return jellyfin_media(s) if s else None


def media_action(mac: str, action: str, value: float = 0) -> None:
    """Play/pause/seek/skip through the app that plays, else the TV's keys."""
    m = media(mac)
    r = remote(mac)
    key = {"play": "KEY_PLAY", "pause": "KEY_PAUSE", "next": "KEY_FF", "prev": "KEY_REWIND",
           "stop": "KEY_STOP"}.get(action)
    if action == "restart":
        action, value = "seek", 0
    if m and m["source"] == "youtube":
        link = youtube_link(mac)
        if action == "toggle":
            link.command("pause" if m.get("state") == "playing" else "play")
        elif action in ("seek", "rel"):
            to = value if action == "seek" else m.get("position", 0) + value
            link.command("seek_to", max(0.0, min(to, m.get("duration") or to)))
        elif action in ("play", "pause", "next", "previous"):
            link.command(action)
        elif action == "prev":
            link.command("previous")
        elif action == "stop":
            r.run(lambda tv: tv.send_key("KEY_RETURN"))
        return
    if m and m["source"] == "jellyfin" and m.get("remote"):
        sid = m["session"]
        cmd = {"toggle": "PlayPause", "play": "Unpause", "pause": "Pause", "next": "NextTrack",
               "prev": "PreviousTrack", "stop": "Stop"}.get(action)
        if action in ("seek", "rel"):
            to = value if action == "seek" else m["position"] + value
            ticks = int(max(0.0, min(to, m["duration"] or to)) * 1e7)
            if jellyfin_command(sid, "Seek", f"?seekPositionTicks={ticks}") or action == "seek":
                return  # no key seeks to a position
        elif cmd and jellyfin_command(sid, cmd):
            return
        if action == "toggle":
            key = "KEY_PAUSE" if m["state"] == "playing" else "KEY_PLAY"
    if action == "toggle" and m:
        key = "KEY_PAUSE" if m.get("state") == "playing" else "KEY_PLAY"
    elif action == "toggle":
        # Nothing reports what plays (an HDMI box, the TV's own player): alternate.
        r.paused = not getattr(r, "paused", False)
        key = "KEY_PAUSE" if r.paused else "KEY_PLAY"
    if action == "rel":
        key = "KEY_FF" if value > 0 else "KEY_REWIND"
    if action == "seek":
        return
    if key:
        r.run(lambda tv: tv.send_key(key))
