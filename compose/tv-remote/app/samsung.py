"""Talking to a Samsung Tizen TV: discovery, the control socket (port 8002),
its REST API (8001), UPnP audio (9197) and Wake-on-LAN. Nothing here knows about
voice or the web page.
"""

from __future__ import annotations

import html
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from samsungtvws import SamsungTVWS
from samsungtvws.remote import RemoteControlCommand

from config import REMOTE_NAME, TV_SUBNET
from store import load_tvs, save_tvs, store_lock, token_file

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Keys the page may send; anything else is refused.
KEYS = {
    "KEY_POWER", "KEY_HOME", "KEY_RETURN", "KEY_MENU", "KEY_SOURCE", "KEY_INFO",
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER",
    "KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_CHUP", "KEY_CHDOWN",
    "KEY_PLAY", "KEY_PAUSE", "KEY_STOP", "KEY_REWIND", "KEY_FF", "KEY_GUIDE",
    "KEY_TV", "KEY_HDMI", "KEY_EXIT",
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


_scan_lock = threading.Lock()
_last_scan: tuple[float, list[dict]] = (0.0, [])
SCAN_REUSE = 5  # seconds a result answers anyone who was waiting for it


def scan() -> list[dict]:
    """One at a time: a scan takes 64 threads, and the container has a limit."""
    global _last_scan
    with _scan_lock:
        if time.monotonic() - _last_scan[0] < SCAN_REUSE:
            return _last_scan[1]
        hosts = [str(h) for h in TV_SUBNET.hosts()]
        with ThreadPoolExecutor(max_workers=64) as pool:
            found = sorted((d for d in pool.map(device_info, hosts) if d), key=lambda d: d["ip"])
        _last_scan = (time.monotonic(), found)
        return found


class Remote:
    """One paired TV: its control connection, rebuilt when it drops."""

    # A quiet control socket goes stale without the client noticing, which lost
    # the first press, and opening a new one takes 1.5 s. So the socket is
    # pinged while the remote is in use, and let go after KEEP seconds unused.
    PING_EVERY = 8
    KEEP = 600
    # After failing, answer at once for a while: keys to a TV that is off would
    # otherwise queue here, each holding a thread for up to 20 s.
    FAIL_FAST = 10
    RELOCATE_EVERY = 60

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.lock = threading.Lock()
        self.tv: SamsungTVWS | None = None
        self.last_used = 0.0
        self.failed_at = -1e9
        self.relocated_at = -1e9
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
        if time.monotonic() - self.relocated_at < self.RELOCATE_EVERY:
            return
        self.relocated_at = time.monotonic()
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

    def _refuse_if_failing(self) -> None:
        if time.monotonic() - self.failed_at < self.FAIL_FAST:
            raise ConnectionError("the TV is not answering; is it on?")

    def run(self, fn):
        """fn(tv) with one reconnect, and one relocation, on failure."""
        self._refuse_if_failing()
        with self.lock:
            self._refuse_if_failing()  # the press ahead in the queue may just have failed
            for attempt in range(3):
                try:
                    started = time.monotonic()
                    fresh = self.tv is None
                    result = fn(self._connect())
                    took = time.monotonic() - started
                    if took > 0.5:
                        log.info("%s: slow command %.2fs (new connection: %s)", self.mac, took, fresh)
                    self.last_used = time.monotonic()
                    self.failed_at = -1e9
                    return result
                except Exception as err:  # noqa: BLE001 - library raises many kinds
                    log.info("%s: attempt %d failed: %s", self.mac, attempt + 1, err)
                    self._drop()
                    if attempt == 1:
                        self._relocate()
            self.failed_at = time.monotonic()
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
    if hit and time.monotonic() - hit[0] < 600 and hit[1]:
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
    if apps:
        _apps_cache[mac] = (time.monotonic(), apps)
        return apps
    if hit and hit[1]:
        return hit[1]
    return [{"id": k, "name": v} for k, v in KNOWN_APPS.items()]


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


def mouse(cmd: dict):
    return lambda tv: tv.send_command(RemoteControlCommand({**cmd, "TypeOfRemote": "ProcessMouseDevice"}), key_press_delay=0)
