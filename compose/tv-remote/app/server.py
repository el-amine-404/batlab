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

import html
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from samsungtvws import SamsungTVWS
from samsungtvws.remote import RemoteControlCommand

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
TV_SUBNET = ipaddress.ip_network(os.environ.get("TV_SUBNET", "192.168.1.0/24"), strict=False)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REMOTE_NAME = os.environ.get("REMOTE_NAME", "batlab remote")
WEB_DIR = Path(__file__).with_name("web")
VERSION = Path(__file__).with_name("VERSION").read_text().strip()

TVS_FILE = DATA_DIR / "tvs.json"
TOKENS_DIR = DATA_DIR / "tokens"
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Keys the page may send; anything else is refused.
KEYS = {
    "KEY_POWER", "KEY_HOME", "KEY_RETURN", "KEY_MENU", "KEY_SOURCE", "KEY_INFO",
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER",
    "KEY_VOLUP", "KEY_VOLDOWN", "KEY_MUTE", "KEY_CHUP", "KEY_CHDOWN",
    "KEY_PLAY", "KEY_PAUSE", "KEY_STOP", "KEY_REWIND", "KEY_FF", "KEY_GUIDE",
    *(f"KEY_{d}" for d in range(10)),
}

# Shortcuts the page offers; each is shown only if installed on that TV.
KNOWN_APPS = {
    "111299001912": "YouTube",
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


# ------------------------------------------------------------------ power

def status(mac: str) -> dict:
    ip = load_tvs()[mac]["ip"]
    dev = device_info(ip, timeout=1.5)
    if not dev:
        return {"power": "off"}
    if dev["power"] != "on":
        return {"power": "standby"}
    return {"power": "on", **audio(ip), "app": app_in_front(mac)}


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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

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
            cache = "no-cache" if file.suffix in (".html", ".webmanifest") else "max-age=3600"
            return self._send(200, file.read_bytes(), STATIC_TYPES[file.suffix], cache)
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
        try:
            if path == "/api/discover":
                paired = load_tvs()
                return self._json(200, [{**d, "paired": d["mac"] in paired} for d in scan()])
            if path == "/api/pair":
                mac, ip = str(body.get("mac", "")).lower(), str(body.get("ip", ""))
                if not MAC_RE.match(mac) or ipaddress.ip_address(ip) not in TV_SUBNET:
                    return self._json(400, {"error": "bad TV"})
                return self._json(200, {"mac": mac, **pair(mac, ip)})
            m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(key|text|enter-text|app|power|mute|cursor|click|forget)", path)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKENS_DIR, 0o700)
    server = ThreadingHTTPServer((LISTEN_ADDR, PORT), Handler)
    server.daemon_threads = True
    log.info("tv-remote %s listening on %s:%d, scanning %s", VERSION, LISTEN_ADDR, PORT, TV_SUBNET)
    server.serve_forever()


if __name__ == "__main__":
    main()
