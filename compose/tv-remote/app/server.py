"""Web remote for Samsung Tizen TVs on the LAN.

Finds TVs by asking every address in TV_SUBNET for the TV's public device
info (port 8001), pairs with one over its token-protected control channel
(port 8002, the TV asks "Allow?" once), and relays remote keys, app launches,
text and Wake-on-LAN from the web page. Standard library plus samsungtvws.

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

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
TV_SUBNET = ipaddress.ip_network(os.environ.get("TV_SUBNET", "192.168.1.0/24"), strict=False)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REMOTE_NAME = os.environ.get("REMOTE_NAME", "batlab remote")
WEB_DIR = Path(__file__).with_name("web")

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
        "power": (dev.get("PowerState") or "on").lower(),
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

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.lock = threading.Lock()
        self.tv: SamsungTVWS | None = None

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
            self.tv = SamsungTVWS(
                host=self._ip(), port=8002, token_file=str(token_file(self.mac)),
                name=REMOTE_NAME, timeout=5, key_press_delay=0,
            )
        return self.tv

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
                    return fn(self._connect())
                except Exception as err:  # noqa: BLE001 - library raises many kinds
                    log.info("%s: attempt %d failed: %s", self.mac, attempt + 1, err)
                    self._drop()
                    if attempt == 1:
                        self._relocate()
            raise ConnectionError("the TV is not answering; is it on?")


remotes: dict[str, Remote] = {}


def remote(mac: str) -> Remote:
    if mac not in load_tvs():
        raise KeyError(mac)
    return remotes.setdefault(mac, Remote(mac))


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


def installed_apps(mac: str) -> list[dict]:
    ip = load_tvs()[mac]["ip"]

    def check(item):
        app_id, name = item
        try:
            with urllib.request.urlopen(f"http://{ip}:8001/api/v2/applications/{app_id}", timeout=2):
                return {"id": app_id, "name": name}
        except (OSError, urllib.error.URLError):
            return None

    with ThreadPoolExecutor(max_workers=len(KNOWN_APPS)) as pool:
        return [a for a in pool.map(check, KNOWN_APPS.items()) if a]


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
        if path == "/api/tvs":
            return self._json(200, [{"mac": m, **t} for m, t in load_tvs().items()])
        m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(status|apps)", path)
        if m:
            mac, what = m.groups()
            if mac not in load_tvs():
                return self._json(404, {"error": "unknown TV"})
            if what == "status":
                dev = device_info(load_tvs()[mac]["ip"], timeout=1.5)
                return self._json(200, {"online": bool(dev), "power": dev["power"] if dev else "off"})
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
            m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(key|text|enter-text|app|wake|forget)", path)
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
                r.run(lambda tv: tv.run_app(app_id))
            elif action == "wake":
                wake(mac)
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
    log.info("listening on %s:%d, scanning %s", LISTEN_ADDR, PORT, TV_SUBNET)
    server.serve_forever()


if __name__ == "__main__":
    main()
