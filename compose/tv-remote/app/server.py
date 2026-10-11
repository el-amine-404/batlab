"""Web remote for Samsung Tizen TVs on the LAN.

Finds TVs by asking every address in TV_SUBNET for the TV's public device
info (port 8001), pairs with one over its token-protected control channel
(port 8002, the TV asks "Allow?" once), and relays keys, text, the cursor and
Wake-on-LAN from the web page. Apps are launched and the app in front is read
over the TV's REST API (port 8001): this TV ignores launches sent over the
control channel. Mute and volume are read and set over UPnP RenderingControl
(port 9197), so the page shows the TV's real state. Standard library plus
samsungtvws.

The page is served behind Caddy (LAN only, basic auth). Requests that change
anything must be JSON with the X-Remote header, which a cross-site form cannot
send without a CORS preflight this server never answers.
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import getpass
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pyytlounge import EventListener, State, YtLoungeApi
from samsungtvws import SamsungTVWS
from samsungtvws.remote import RemoteControlCommand

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
TV_SUBNET = ipaddress.ip_network(os.environ.get("TV_SUBNET", "192.168.1.0/24"), strict=False)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REMOTE_NAME = os.environ.get("REMOTE_NAME", "batlab remote")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
WEB_DIR = Path(__file__).with_name("web")
VERSION = Path(__file__).with_name("VERSION").read_text().strip()

TVS_FILE = DATA_DIR / "tvs.json"
TOKENS_DIR = DATA_DIR / "tokens"
YOUTUBE_URL_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})(?:[?&#]|$)")
# A bare ID; an 11-letter search word looks the same unless it has a digit,
# - or _, or a capital after a small letter, as generated IDs nearly always do.
YOUTUBE_BARE_ID = re.compile(r"^(?=.*(?:[0-9_-]|[a-z].*[A-Z]))[A-Za-z0-9_-]{11}$")

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Keys the page may send; anything else is refused.
KEYS = {
    "KEY_POWER", "KEY_HOME", "KEY_RETURN", "KEY_MENU", "KEY_SOURCE", "KEY_INFO",
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER",
    "KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_CHUP", "KEY_CHDOWN",
    "KEY_PLAY", "KEY_PAUSE", "KEY_STOP", "KEY_REWIND", "KEY_FF", "KEY_GUIDE",
    "KEY_TV",
    *(f"KEY_{d}" for d in range(10)),
}

# Shortcuts the page offers; each is shown only if installed on that TV. TVs
# from 2020 on no longer list their apps, so an app is only found by its ID
# (3202010022079, the browser, checked on the 2025 Q8F).
KNOWN_APPS = {
    "111299001912": "YouTube",
    "3202010022079": "Internet",
    "AprZAARz4r.Jellyfin": "Jellyfin",
    "3201907018807": "Netflix",
    "3201910019365": "Prime Video",
    "3201901017640": "Disney+",
    "3201606009684": "Spotify",
}

log = logging.getLogger("tv-remote")
store_lock = threading.Lock()


# ---------------------------------------------------------------- storage

def load_tvs() -> dict[str, dict]:
    try:
        return json.loads(TVS_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_tvs(tvs: dict[str, dict]) -> None:
    tmp = TVS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tvs, indent=2))
    tmp.replace(TVS_FILE)


def token_file(mac: str) -> Path:
    return TOKENS_DIR / (mac.replace(":", "") + ".token")


# -------------------------------------------------------------- discovery

def device_info(ip: str, timeout: float = 0.8) -> dict | None:
    """The TV's public info page, or None when nothing Samsung answers there."""
    try:
        with urllib.request.urlopen(f"http://{ip}:8001/api/v2/", timeout=timeout) as r:
            body = json.load(r)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    dev = body.get("device") or {}
    mac = (dev.get("wifiMac") or dev.get("wiredMac") or "").lower()
    if dev.get("OS") != "Tizen" or not MAC_RE.match(mac):
        return None
    return {
        "mac": mac,
        "ip": ip,
        "name": html.unescape(dev.get("name") or "Samsung TV"),
        "model": dev.get("modelName") or "",
        "power": "on" if (dev.get("PowerState") or "on").lower() == "on" else "standby",
        "tokenAuth": dev.get("TokenAuthSupport") == "true",
    }


def scan() -> list[dict]:
    hosts = [str(h) for h in TV_SUBNET.hosts()]
    with ThreadPoolExecutor(max_workers=64) as pool:
        found = [d for d in pool.map(device_info, hosts) if d]
    return sorted(found, key=lambda d: d["ip"])


# ---------------------------------------------------------------- remotes

class Remote:
    """One paired TV: its control connection, rebuilt when it drops."""

    # A quiet control socket goes stale without the client noticing, which lost
    # the first press, and opening a new one takes 1.5 s. So the socket is
    # pinged while the remote is in use, and let go after KEEP seconds unused.
    PING_EVERY = 8
    KEEP = 600

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.lock = threading.Lock()
        self.tv: SamsungTVWS | None = None
        self.last_used = 0.0
        threading.Thread(target=self._keepalive, daemon=True).start()

    def _keepalive(self) -> None:
        while True:
            time.sleep(self.PING_EVERY)
            with self.lock:
                if self.tv is None:
                    continue
                if time.monotonic() - self.last_used > self.KEEP:
                    self._drop()
                    continue
                try:
                    if not self.tv.is_alive():
                        raise ConnectionError("closed by the TV")
                    self.tv.connection.ping()
                except Exception as err:  # noqa: BLE001 - stale socket: reopen on next use
                    log.info("%s: keepalive dropped the connection: %s", self.mac, err)
                    self._drop()

    def _ip(self) -> str:
        return load_tvs()[self.mac]["ip"]

    def _relocate(self) -> None:
        """The TV may have a new address: look for its MAC on the subnet."""
        for dev in scan():
            if dev["mac"] == self.mac:
                with store_lock:
                    tvs = load_tvs()
                    tvs[self.mac]["ip"] = dev["ip"]
                    save_tvs(tvs)
                return

    def _connect(self) -> SamsungTVWS:
        if self.tv is None:
            tv = SamsungTVWS(
                host=self._ip(), port=8002, token_file=str(token_file(self.mac)),
                name=REMOTE_NAME, timeout=5, key_press_delay=0,
            )
            # The 5 s timeout bounds connecting to a TV that is off; on the
            # open socket it would also end reads after 5 quiet seconds and
            # break it, so reads wait without one, on a thread that drains
            # what the TV sends.
            tv.connection = tv.open()
            tv.connection.settimeout(None)
            threading.Thread(target=self._drain, args=(tv.connection,), daemon=True).start()
            self.tv = tv
        return self.tv

    @staticmethod
    def _drain(conn) -> None:
        try:
            while conn.recv():
                pass
        except Exception:  # noqa: BLE001 - closed: the keepalive notices
            pass

    def _drop(self) -> None:
        if self.tv is not None:
            try:
                self.tv.close()
            except Exception:  # noqa: BLE001 - closing a dead socket
                pass
        self.tv = None

    def run(self, fn):
        """fn(tv) with one reconnect, and one relocation, on failure."""
        with self.lock:
            for attempt in range(3):
                try:
                    started = time.monotonic()
                    fresh = self.tv is None
                    result = fn(self._connect())
                    took = time.monotonic() - started
                    if took > 0.5:
                        log.info("%s: slow command %.2fs (new connection: %s)", self.mac, took, fresh)
                    self.last_used = time.monotonic()
                    return result
                except Exception as err:  # noqa: BLE001 - library raises many kinds
                    log.info("%s: attempt %d failed: %s", self.mac, attempt + 1, err)
                    self._drop()
                    if attempt == 1:
                        self._relocate()
            raise ConnectionError("the TV is not answering; is it on?")


remotes: dict[str, Remote] = {}


remotes_lock = threading.Lock()


def remote(mac: str) -> Remote:
    if mac not in load_tvs():
        raise KeyError(mac)
    with remotes_lock:
        if mac not in remotes:  # one Remote, and one keepalive thread, per TV
            remotes[mac] = Remote(mac)
        return remotes[mac]


def pair(mac: str, ip: str) -> dict:
    dev = device_info(ip, timeout=3)
    if not dev or dev["mac"] != mac:
        raise ConnectionError("no Samsung TV with that address answered")
    with store_lock:
        tvs = load_tvs()
        tvs[mac] = {k: dev[k] for k in ("ip", "name", "model")}
        save_tvs(tvs)
    remotes.pop(mac, None)
    tf = token_file(mac)
    tv = SamsungTVWS(host=ip, port=8002, token_file=str(tf), name=REMOTE_NAME, timeout=30)
    try:
        tv.open()  # the TV shows "Allow?" and answers once it is pressed
    except Exception as err:  # noqa: BLE001
        with store_lock:
            tvs = load_tvs()
            tvs.pop(mac, None)
            save_tvs(tvs)
        raise ConnectionError("the TV did not allow the connection") from err
    finally:
        try:
            tv.close()
        except Exception:  # noqa: BLE001
            pass
    if tf.exists():
        tf.chmod(0o600)
    return tvs[mac]


def wake(mac: str) -> None:
    """Wake-on-LAN; works when the TV allows it (Power On with Mobile)."""
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    broadcast = str(TV_SUBNET.broadcast_address)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(packet, (broadcast, 9))
            time.sleep(0.1)


def rest(ip: str, path: str, method: str = "GET", timeout: float = 3):
    req = urllib.request.Request(f"http://{ip}:8001/api/v2/{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


_apps_cache: dict[str, tuple[float, list[dict]]] = {}


def installed_apps(mac: str) -> list[dict]:
    """KNOWN_APPS present on the TV; asked again every 10 minutes."""
    hit = _apps_cache.get(mac)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    ip = load_tvs()[mac]["ip"]

    def check(item):
        app_id, name = item
        try:
            rest(ip, f"applications/{app_id}", timeout=2)
            return {"id": app_id, "name": name}
        except (OSError, ValueError, urllib.error.URLError):
            return None

    with ThreadPoolExecutor(max_workers=len(KNOWN_APPS)) as pool:
        apps = [a for a in pool.map(check, KNOWN_APPS.items()) if a]
    _apps_cache[mac] = (time.monotonic(), apps)
    return apps


def app_in_front(mac: str) -> str | None:
    ip = load_tvs()[mac]["ip"]

    def visible(app):
        try:
            st = rest(ip, f"applications/{app['id']}", timeout=2)
            return app["name"] if st.get("visible") else None
        except (OSError, ValueError, urllib.error.URLError):
            return None

    apps = installed_apps(mac)
    if not apps:
        return None
    with ThreadPoolExecutor(max_workers=len(apps)) as pool:
        return next((n for n in pool.map(visible, apps) if n), None)


# ------------------------------------------------------------- UPnP audio

RC = "urn:schemas-upnp-org:service:RenderingControl:1"


def upnp(ip: str, action: str, extra: str = "") -> str:
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{RC}"><InstanceID>0</InstanceID><Channel>Master</Channel>{extra}'
        f"</u:{action}></s:Body></s:Envelope>"
    ).encode()
    req = urllib.request.Request(
        f"http://{ip}:9197/upnp/control/RenderingControl1", data=body, method="POST",
        headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{RC}#{action}"'},
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.read().decode("utf-8", "replace")


def audio(ip: str) -> dict:
    try:
        mute = re.search(r"<CurrentMute>(\d)<", upnp(ip, "GetMute"))
        vol = re.search(r"<CurrentVolume>(\d+)<", upnp(ip, "GetVolume"))
        return {"mute": bool(mute and mute.group(1) == "1"), "volume": int(vol.group(1)) if vol else None}
    except (OSError, urllib.error.URLError):
        return {"mute": None, "volume": None}


def set_mute(ip: str, mute: bool) -> None:
    upnp(ip, "SetMute", f"<DesiredMute>{int(mute)}</DesiredMute>")


def set_volume(ip: str, level: int) -> None:
    upnp(ip, "SetVolume", f"<DesiredVolume>{level}</DesiredVolume>")


# ------------------------------------------------------------ async loop
# pyytlounge is asyncio; it runs on one loop in its own thread, and request
# handlers hand it work with run_coroutine_threadsafe.

LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True, name="asyncio").start()


def on_loop(coro, timeout: float = 15):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


# ---------------------------------------------------------------- YouTube

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
    with remotes_lock:
        if mac not in links:
            links[mac] = YouTubeLink(mac)
        return links[mac]


def youtube_search(ip: str, text: str) -> None:
    """Open YouTube's search on the TV over DIAL; checked to work on the Q8F."""
    req = urllib.request.Request(
        f"http://{ip}:8080/ws/apps/YouTube",
        data=("search_query=" + urllib.parse.quote(text.strip())).encode(), method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=8):
        pass


# --------------------------------------------------------------- Jellyfin

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
        # Not playing: count it as open only shortly after its last request,
        # or "Now: Jellyfin" stays long after leaving it.
        try:
            last = calendar.timegm(time.strptime(tv["LastActivityDate"][:19], "%Y-%m-%dT%H:%M:%S"))
            if time.time() - last > 60:
                tv = None
        except (KeyError, ValueError):
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


# ------------------------------------------------------------------ media

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


# ------------------------------------------------------------------ power

def status(mac: str) -> dict:
    ip = load_tvs()[mac]["ip"]
    dev = device_info(ip, timeout=1.5)
    if not dev:
        return {"power": "off"}
    if dev["power"] != "on":
        return {"power": "standby"}
    app = app_in_front(mac)
    jf = jellyfin_session()
    m = media(mac)
    if not app and (jf or (m and m["source"] == "jellyfin")):
        app = "Jellyfin"  # no app ID for the store version; its server knows it is open
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


# ----------------------------------------------------------------- cursor

def mouse(cmd: dict):
    return lambda tv: tv.send_command(RemoteControlCommand({**cmd, "TypeOfRemote": "ProcessMouseDevice"}), key_press_delay=0)


# ------------------------------------------------------------------- login
# A login of the page's own, not Caddy's basic auth: a phone's home-screen
# app cannot show the browser's password prompt (iOS opened a black screen,
# Android could not fetch the icon). The password is stored as an scrypt hash;
# a session is a signed cookie, so nothing is kept per device.

AUTH_FILE = DATA_DIR / "auth.json"
SESSION_DAYS = 180
_fails: dict[str, list[float]] = {}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20, dklen=32)


def set_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    data = {"salt": salt.hex(), "hash": _scrypt(password, salt).hex(),
            "secret": secrets.token_hex(32)}  # a new secret logs every device out
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    tmp.replace(AUTH_FILE)


def _auth() -> dict | None:
    try:
        return json.loads(AUTH_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def check_password(password: str) -> bool:
    a = _auth()
    return bool(a) and hmac.compare_digest(_scrypt(password, bytes.fromhex(a["salt"])).hex(), a["hash"])


def make_session() -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(bytes.fromhex(_auth()["secret"]), exp.encode(), "sha256").hexdigest()
    return f"{exp}.{sig}"


def valid_session(cookie_header: str | None) -> bool:
    a = _auth()
    if not a or not cookie_header:
        return False
    m = re.search(r"(?:^|;\s*)tvr=(\d+)\.([0-9a-f]{64})", cookie_header)
    if not m or int(m.group(1)) < time.time():
        return False
    want = hmac.new(bytes.fromhex(a["secret"]), m.group(1).encode(), "sha256").hexdigest()
    return hmac.compare_digest(want, m.group(2))


def too_many_tries(who: str) -> bool:
    """At most 10 wrong passwords per address in 10 minutes."""
    now = time.time()
    tries = [t for t in _fails.get(who, []) if now - t < 600]
    _fails[who] = tries
    return len(tries) >= 10


# ------------------------------------------------------------------- HTTP

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".webmanifest": "application/manifest+json",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "tv-remote"
    sys_version = ""

    def log_message(self, fmt, *args):  # through logging, without the noise
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in getattr(self, "_extra_headers", []):
            self.send_header(k, v)
        self._extra_headers = []
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj, cookie: str | None = None) -> None:
        if cookie is not None:
            self._extra_headers = [("Set-Cookie", cookie)]
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _client(self) -> str:
        # Behind Caddy: the client's address is the last hop it adds.
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[-1].strip() or self.client_address[0]

    def _authed(self) -> bool:
        return valid_session(self.headers.get("Cookie"))

    def _body(self) -> dict:
        if self.headers.get("X-Remote") != "1" or not (
            self.headers.get("Content-Type", "").startswith("application/json")
        ):
            raise PermissionError
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    # GET: the page, its files, and read-only state
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if path == "/api/info":
            return self._json(200, {"version": VERSION})
        if path == "/api/me":
            if not _auth():
                return self._json(503, {"error": "no password set", "setup": True})
            return self._json(200 if self._authed() else 401, {"ok": self._authed()})
        if path == "/reload":
            # Clear-Site-Data drops this site's cache (Chrome, Firefox, Safari 17+),
            # then the page loads fresh; for phones where clearing is hard.
            self._extra_headers = [("Clear-Site-Data", '"cache"')]
            return self._send(200, b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/">'
                              b"<title>Reloading</title><p>Reloading...</p>", "text/html; charset=utf-8")
        if path.startswith("/api/") and not self._authed():
            return self._json(401, {"error": "log in"})
        if path == "/api/tvs":
            return self._json(200, [{"mac": m, **t} for m, t in load_tvs().items()])
        m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(status|apps)", path)
        if m:
            mac, what = m.groups()
            if mac not in load_tvs():
                return self._json(404, {"error": "unknown TV"})
            if what == "status":
                st = status(mac)
                r = remote(mac)
                if st["power"] == "on" and r.tv is None and not r.lock.locked():
                    # The page is open: connect now, so the first press is quick.
                    threading.Thread(target=r.run, args=(lambda tv: None,), daemon=True).start()
                return self._json(200, st)
            return self._json(200, installed_apps(mac))
        name = "index.html" if path == "/" else path.lstrip("/")
        file = (WEB_DIR / name).resolve()
        if file.parent == WEB_DIR.resolve() and file.is_file() and file.suffix in STATIC_TYPES:
            body = file.read_bytes()
            if file.name == "index.html":
                # The version in every asset URL: a new release is a new URL,
                # so no browser can mix an old script with new HTML.
                body = re.sub(rb'(href|src)="(style\.css|app\.js)"',
                              lambda m: m.group(1) + b'="' + m.group(2) + b"?v=" + VERSION.encode() + b'"', body)
            return self._send(200, body, STATIC_TYPES[file.suffix], "no-cache")
        self._json(404, {"error": "not found"})

    # POST: everything that does something
    def do_POST(self) -> None:
        try:
            body = self._body()
        except PermissionError:
            return self._json(403, {"error": "forbidden"})
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            who = self._client()
            if not _auth():
                return self._json(503, {"error": "no password set", "setup": True})
            if too_many_tries(who):
                return self._json(429, {"error": "too many tries; wait 10 minutes"})
            if not check_password(str(body.get("password", ""))):
                _fails.setdefault(who, []).append(time.time())
                time.sleep(1)
                return self._json(401, {"error": "wrong password"})
            _fails.pop(who, None)
            cookie = f"tvr={make_session()}; Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; SameSite=Lax"
            return self._json(200, {"ok": True}, cookie)
        if path == "/api/logout":
            return self._json(200, {"ok": True}, "tvr=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        if not self._authed():
            return self._json(401, {"error": "log in"})
        try:
            if path == "/api/discover":
                paired = load_tvs()
                return self._json(200, [{**d, "paired": d["mac"] in paired} for d in scan()])
            if path == "/api/pair":
                mac, ip = str(body.get("mac", "")).lower(), str(body.get("ip", ""))
                if not MAC_RE.match(mac) or ipaddress.ip_address(ip) not in TV_SUBNET:
                    return self._json(400, {"error": "bad TV"})
                return self._json(200, {"mac": mac, **pair(mac, ip)})
            m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(key|text|enter-text|app|power|mute|volume|media|youtube|cursor|click|forget)", path)
            if not m:
                return self._json(404, {"error": "not found"})
            mac, action = m.groups()
            r = remote(mac)
            if action == "key":
                key = str(body.get("key", ""))
                if key not in KEYS:
                    return self._json(400, {"error": "unknown key"})
                r.run(lambda tv: tv.send_key(key))
            elif action == "text":
                text = str(body.get("text", ""))[:500]
                r.run(lambda tv: tv.send_text(text))
            elif action == "enter-text":
                r.run(lambda tv: tv.end_text())
            elif action == "app":
                app_id = str(body.get("id", ""))
                if app_id not in KNOWN_APPS:
                    return self._json(400, {"error": "unknown app"})
                try:
                    rest(load_tvs()[mac]["ip"], f"applications/{app_id}", "POST", timeout=6)
                except (OSError, urllib.error.URLError) as err:
                    raise ConnectionError("the TV did not open the app") from err
            elif action == "power":
                return self._json(200, power(mac))
            elif action == "mute":
                ip = load_tvs()[mac]["ip"]
                now = audio(ip)["mute"]
                if now is None:
                    r.run(lambda tv: tv.send_key("KEY_MUTE"))
                else:
                    set_mute(ip, not now)
                return self._json(200, audio(ip))
            elif action == "volume":
                level = max(0, min(100, int(body.get("level", 0))))
                ip = load_tvs()[mac]["ip"]
                set_volume(ip, level)
                return self._json(200, audio(ip))
            elif action == "media":
                what = str(body.get("action", ""))
                if what not in ("toggle", "play", "pause", "seek", "rel", "next", "prev", "stop"):
                    return self._json(400, {"error": "unknown media action"})
                media_action(mac, what, float(body.get("value", 0)))
                return self._json(200, {"ok": True, "media": media(mac)})
            elif action == "youtube":
                text = str(body.get("text", ""))[:300]
                if not text.strip():
                    return self._json(400, {"error": "type a link or a search"})
                t = text.strip()
                m = YOUTUBE_URL_ID.search(t)
                vid = m.group(1) if m else (t if YOUTUBE_BARE_ID.match(t) else None)
                try:
                    if vid:
                        youtube_link(mac).play_video(vid)
                        return self._json(200, {"ok": True, "did": "video"})
                    youtube_search(load_tvs()[mac]["ip"], t)
                    return self._json(200, {"ok": True, "did": "search"})
                except (OSError, urllib.error.URLError, TimeoutError) as err:
                    raise ConnectionError("YouTube on the TV did not answer") from err
            elif action == "cursor":
                dx, dy = int(body.get("dx", 0)), int(body.get("dy", 0))
                dx, dy = max(-400, min(400, dx)), max(-400, min(400, dy))
                if dx or dy:
                    r.run(mouse({"Cmd": "Move", "Position": {"x": dx, "y": dy, "Time": "0"}}))
            elif action == "click":
                r.run(mouse({"Cmd": "LeftClick"}))
            elif action == "forget":
                with store_lock:
                    tvs = load_tvs()
                    tvs.pop(mac, None)
                    save_tvs(tvs)
                remotes.pop(mac, None)
                token_file(mac).unlink(missing_ok=True)
            return self._json(200, {"ok": True})
        except KeyError:
            return self._json(404, {"error": "unknown TV"})
        except ConnectionError as err:
            return self._json(502, {"error": str(err)})
        except Exception:  # noqa: BLE001 - never leak a traceback to the page
            log.exception("request failed: %s", path)
            return self._json(500, {"error": "something went wrong"})


def main() -> None:
    if sys.argv[1:] == ["set-password"]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        p1 = getpass.getpass("New password for the TV remote: ")
        p2 = getpass.getpass("Again: ")
        if p1 != p2 or len(p1) < 10:
            sys.exit("the passwords differ, or are under 10 characters; nothing changed")
        set_password(p1)
        print("password set; every device must log in again")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKENS_DIR, 0o700)
    server = ThreadingHTTPServer((LISTEN_ADDR, PORT), Handler)
    server.daemon_threads = True
    log.info("tv-remote %s listening on %s:%d, scanning %s", VERSION, LISTEN_ADDR, PORT, TV_SUBNET)
    server.serve_forever()


if __name__ == "__main__":
    main()
