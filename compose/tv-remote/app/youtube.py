"""YouTube on the TV, through the Lounge API casting clients use, and search."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pyytlounge import EventListener, State, YtLoungeApi

from config import DATA_DIR, REMOTE_NAME
from samsung import rest
from store import load_tvs

log = logging.getLogger("tv-remote")
links_lock = threading.Lock()

YOUTUBE_URL_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})(?:[?&#]|$)")
# A bare ID; an 11-letter search word looks the same unless it has a digit,
# - or _, or a capital after a small letter, as generated IDs nearly always do.
YOUTUBE_BARE_ID = re.compile(r"^(?=.*(?:[0-9_-]|[a-z].*[A-Z]))[A-Za-z0-9_-]{11}$")


def video_id_in(text: str) -> str | None:
    """The video a link or bare ID names; None for anything else."""
    m = YOUTUBE_URL_ID.search(text)
    return m.group(1) if m else (text if YOUTUBE_BARE_ID.match(text) else None)


# pyytlounge is asyncio; it runs on one loop in its own thread, and request
# handlers hand it work with run_coroutine_threadsafe.

LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True, name="asyncio").start()


def on_loop(coro, timeout: float = 15):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


def dial_youtube(ip: str) -> tuple[str, str | None]:
    """The YouTube app's DIAL state (running, stopped) and, while it runs,
    the screenId a casting client pairs with."""
    with urllib.request.urlopen(f"http://{ip}:8080/ws/apps/YouTube", timeout=3) as r:
        x = r.read().decode("utf-8", "replace")
    state = re.search(r"<state>([^<]+)</state>", x)
    screen = re.search(r"<screenId>([^<]+)</screenId>", x)
    return (state.group(1) if state else "unknown", screen.group(1) if screen else None)


def youtube_title(video_id: str) -> tuple[str | None, str | None]:
    try:
        q = urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}", safe="")
        with urllib.request.urlopen(f"https://www.youtube.com/oembed?format=json&url={q}", timeout=5) as r:
            d = json.load(r)
        return d.get("title"), d.get("author_name")
    except (OSError, ValueError, urllib.error.URLError):
        return None, None


class YouTubeLink(EventListener):
    """The TV's YouTube app as a casting client sees it: what plays, where,
    and commands to it, over YouTube's Lounge API (pyytlounge). Pairs by
    itself through DIAL the first time YouTube is open on the TV; the pairing
    is kept in DATA_DIR. Polls the TV's DIAL (local) while YouTube is closed."""

    def __init__(self, mac: str) -> None:
        super().__init__()
        self.mac = mac
        self.api: YtLoungeApi | None = None
        self.connected = False
        self.hurry = asyncio.Event()  # a command waits: poll fast
        self.media: dict = {}
        self.auth_file = DATA_DIR / f"youtube-{mac.replace(':', '')}.json"
        asyncio.run_coroutine_threadsafe(self._run(), LOOP)

    # events from the TV
    async def now_playing_changed(self, event) -> None:
        vid = event.video_id
        if not vid:
            self.media = {}
            return
        if vid != self.media.get("id"):
            title, channel = await asyncio.to_thread(youtube_title, vid)
            self.media = {"id": vid, "title": title or "YouTube", "subtitle": channel or ""}
        self._update(event.state, event.current_time, event.duration)

    async def playback_state_changed(self, event) -> None:
        self._update(event.state, event.current_time, event.duration)

    async def disconnected(self, event) -> None:
        self.connected = False
        self.media = {}

    def _update(self, state, position, duration) -> None:
        names = {State.Playing: "playing", State.Paused: "paused", State.Starting: "buffering",
                 State.Buffering: "ended", State.Advertisement: "ad", State.Cued: "buffering"}
        self.media["state"] = names.get(state, "stopped")
        if position is not None:
            self.media["position"] = position
        if duration:
            self.media["duration"] = duration
        self.media["at"] = time.time()

    def snapshot(self) -> dict | None:
        if not self.connected or not self.media.get("id"):
            return None
        m = dict(self.media)
        if m.get("state") == "playing" and "position" in m:  # extrapolate between events
            m["position"] = min(m.get("duration") or 1e9, m["position"] + time.time() - m["at"])
        return {"source": "youtube", "can_seek": True, "can_skip": True, **m}

    # the connection
    def _save(self) -> None:
        # auth.serialize(), not store_auth_state(): the latter drops "version",
        # which load_auth_state() requires (pyytlounge 3.4.0).
        if self.api:
            self.auth_file.write_text(json.dumps(self.api.auth.serialize()))
            self.auth_file.chmod(0o600)

    async def _run(self) -> None:
        while True:
            try:
                await self._session()
            except Exception as err:  # noqa: BLE001 - keep trying, quietly
                log.info("%s: YouTube link: %s", self.mac, err)
            self.connected = False
            # Every 10 s; every 2 s while a link waits to be played.
            try:
                await asyncio.wait_for(self.hurry.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(2)

    async def _session(self) -> None:
        if self.mac not in load_tvs():
            return
        ip = load_tvs()[self.mac]["ip"]
        state, screen_id = await asyncio.to_thread(dial_youtube, ip)
        if state != "running":
            return  # YouTube is closed: nothing to connect to
        async with YtLoungeApi(REMOTE_NAME, self) as api:
            self.api = api
            try:
                if self.auth_file.exists():
                    try:
                        api.load_auth_state(json.loads(self.auth_file.read_text()))
                    except (KeyError, ValueError):
                        log.info("%s: YouTube pairing unreadable, pairing again", self.mac)
                        self.auth_file.unlink(missing_ok=True)
                if not (api.paired() and await api.refresh_auth()):
                    if not screen_id or not await api.pair_with_screen_id(screen_id):
                        return
                self._save()
                if not await api.connect():
                    return
                self.connected = True
                self.hurry.clear()
                log.info("%s: YouTube link up", self.mac)
                await api.subscribe()  # until the app closes or the link drops
            finally:
                self.connected = False
                self.api = None

    # commands
    def command(self, name: str, *args):
        if not self.connected or not self.api:
            raise ConnectionError("YouTube is not connected yet")
        return on_loop(getattr(self.api, name)(*args))

    def play_video(self, video_id: str) -> None:
        """Opens YouTube if needed, waits for the link, then plays."""
        if not self.connected:
            ip = load_tvs()[self.mac]["ip"]
            rest(ip, "applications/111299001912", "POST", timeout=6)
            LOOP.call_soon_threadsafe(self.hurry.set)
            deadline = time.monotonic() + 30
            while not self.connected and time.monotonic() < deadline:
                time.sleep(0.5)
            LOOP.call_soon_threadsafe(self.hurry.clear)
        if not self.connected:
            raise ConnectionError("YouTube opened, but the casting link did not come up")
        self.command("play_video", video_id)


links: dict[str, YouTubeLink] = {}


def youtube_link(mac: str) -> YouTubeLink:
    with links_lock:
        if mac not in links:
            links[mac] = YouTubeLink(mac)
        return links[mac]


# Search runs on lab2, not on the TV: the TV refuses every DIAL launch (403,
# checked 2026-10-11), and the Lounge API that plays links has no search.
# The endpoint is the one YouTube's website uses: no API key, but not a
# documented API either, so it may change.
SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
SEARCH_CLIENT = {"clientName": "WEB", "clientVersion": "2.20260901.00.00", "hl": "en"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_thumbs: dict[str, bytes] = {}


def _runs(o: dict) -> str:
    return "".join(r.get("text", "") for r in (o or {}).get("runs", [])) or (o or {}).get("simpleText", "")


def youtube_search(query: str, limit: int = 20) -> list[dict]:
    body = {"context": {"client": SEARCH_CLIENT}, "query": query, "params": "EgIQAQ%3D%3D"}  # videos only
    req = urllib.request.Request(SEARCH_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                yield o["videoRenderer"]
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    out = []
    for v in walk(data):
        if not VIDEO_ID.match(v.get("videoId", "")):
            continue
        out.append({
            "id": v["videoId"], "title": _runs(v.get("title")), "channel": _runs(v.get("ownerText")),
            "length": (v.get("lengthText") or {}).get("simpleText", ""),
        })
        if len(out) >= limit:
            break
    return out


def youtube_thumb(video_id: str) -> bytes:
    """Fetched by lab2, so phones never contact Google and the page keeps
    img-src 'self'. A few hundred small JPEGs at most, kept in memory."""
    if video_id not in _thumbs:
        with urllib.request.urlopen(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", timeout=8) as r:
            data = r.read(200_000)
        if len(_thumbs) > 300:
            _thumbs.clear()
        _thumbs[video_id] = data
    return _thumbs[video_id]
