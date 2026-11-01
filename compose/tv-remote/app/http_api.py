"""The web page's API and its files. Thin: it checks a request, calls one thing,
and answers. Requests that change anything must be JSON with the X-Remote header,
which a cross-site form cannot send without a CORS preflight this server never
answers.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler

import auth
import livestate
import samsung
import speech
import tvcontrol
import voice
import youtube
from commands import run_intent
from config import TV_SUBNET, VERSION, WEB_DIR
from store import load_tvs, save_tvs, store_lock, token_file

log = logging.getLogger("tv-remote")

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".webmanifest": "application/manifest+json",
}




def _key(tv, body, mac):
    key = str(body.get("key", ""))
    if key not in samsung.KEYS:
        return 400, {"error": "unknown key"}
    tv.press(key)


def _text(tv, body, mac):
    text = str(body.get("text", ""))[:500]
    samsung.remote(mac).run(lambda t: t.send_text(text))


def _enter_text(tv, body, mac):
    samsung.remote(mac).run(lambda t: t.end_text())


def _wake_if_off(tv):
    if tv.status().get("power") != "on":
        tv.toggle_power()
        time.sleep(2)


def _app(tv, body, mac):
    app_id = str(body.get("id", ""))
    if app_id not in samsung.KNOWN_APPS:
        return 400, {"error": "unknown app"}
    try:
        _wake_if_off(tv)
    except ConnectionError as err:
        log.warning("failed to wake the TV before opening %s: %s", app_id, err)
    tv.open_app(app_id)


def _input(tv, body, mac):
    _wake_if_off(tv)
    if body.get("next"):
        tv.press("KEY_HDMI")
        return 200, {"name": "next HDMI"}
    name = tv.select_input(str(body.get("id", "")))
    return (200, {"name": name}) if name else (400, {"error": "unknown input"})


def _power(tv, body, mac):
    return 200, tv.toggle_power()


def _mute(tv, body, mac):
    now = tv.audio()["mute"]
    if now is None:
        tv.press("KEY_MUTE")
    else:
        tv.set_mute(not now)
    return 200, tv.audio()


def _volume(tv, body, mac):
    tv.set_volume(max(0, min(100, int(body.get("level", 0)))))
    return 200, tv.audio()


def _media(tv, body, mac):
    what = str(body.get("action", ""))
    if what not in ("toggle", "play", "pause", "seek", "rel", "next", "prev", "stop"):
        return 400, {"error": "unknown media action"}
    tv.media_action(what, float(body.get("value", 0)))
    return 200, {"ok": True, "media": tv.media()}


def _youtube(tv, body, mac):
    text = str(body.get("text", ""))[:300].strip()
    if not text:
        return 400, {"error": "type a link or a search"}
    vid = youtube.video_id_in(text)
    if not vid:
        return 400, {"error": "not a video link; search instead"}
    tv.play_video(vid)
    return 200, {"ok": True, "did": "video"}


def _cursor(tv, body, mac):
    dx = max(-400, min(400, int(body.get("dx", 0))))
    dy = max(-400, min(400, int(body.get("dy", 0))))
    if dx or dy:
        samsung.remote(mac).run(samsung.mouse({"Cmd": "Move", "Position": {"x": dx, "y": dy, "Time": "0"}}))


def _click(tv, body, mac):
    samsung.remote(mac).run(samsung.mouse({"Cmd": "LeftClick"}))


def _forget(tv, body, mac):
    if body.get("confirm") is not True:  # pairing again needs someone at the TV
        return 400, {"error": "confirm is required"}
    with store_lock:
        tvs = load_tvs()
        tvs.pop(mac, None)
        save_tvs(tvs)
    samsung.remotes.pop(mac, None)
    token_file(mac).unlink(missing_ok=True)


def _command(tv, body, mac):
    text = str(body.get("text", ""))[:300]
    intent = voice.parse(text, tv.inputs())
    out = {"text": text, "intent": intent}
    if intent:
        out.update(run_intent(tv, intent))
    return 200, out


def _sleep(tv, body, mac):
    minutes = int(body.get("minutes", 0))
    if minutes <= 0:
        tv.cancel_sleep()
    else:
        tv.sleep_in(minutes)


def _listening(tv, body, mac):
    if body.get("on"):
        speech.duck.lower(mac, tv)
    else:
        speech.duck.restore(mac)


# POST /api/tv/<mac>/<action>. An action returns (status, body), or None for "ok".
ACTIONS = {
    "key": _key, "text": _text, "enter-text": _enter_text, "app": _app, "input": _input, "power": _power,
    "mute": _mute, "volume": _volume, "media": _media, "youtube": _youtube, "cursor": _cursor, "click": _click,
    "forget": _forget, "command": _command, "sleep": _sleep, "listening": _listening,
}


def _live(mac: str):
    return livestate.live(mac, lambda: tvcontrol.control(mac))


def _status(tv, mac):
    st = _live(mac).current()
    r = samsung.remote(mac)
    if st["power"] == "on" and r.tv is None and not r.lock.locked():
        # The page is open: connect now, so the first press is quick.
        threading.Thread(target=r.run, args=(lambda t: None,), daemon=True).start()
    return st


MAX_STREAMS = 16
_streams = threading.BoundedSemaphore(MAX_STREAMS)


# GET /api/tv/<mac>/<what>. "media" is light: polled every second while a card shows.
READS = {
    "status": _status,
    "apps": lambda tv, mac: samsung.installed_apps(mac),
    "media": lambda tv, mac: {"media": tv.media()},
    "inputs": lambda tv, mac: [{k: i[k] for k in i if k != "aliases"} for i in tv.inputs()],
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
        return auth.valid_session(self.headers.get("Cookie"))

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
            if not auth.password_is_set():
                return self._json(503, {"error": "no password set", "setup": True})
            if not self._authed():
                return self._json(401, {"ok": False})
            cookie = self.headers.get("Cookie")
            renewed = auth.renew_session(cookie)
            return self._json(200, {"ok": True}, self._session_cookie(auth.session_cookie_value(cookie)) if renewed else None)
        if path == "/reload":
            # Clear-Site-Data drops this site's cache (Chrome, Firefox, Safari 17+),
            # then the page loads fresh; for phones where clearing is hard.
            self._extra_headers = [("Clear-Site-Data", '"cache"')]
            return self._send(200, b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/">'
                              b"<title>Reloading</title><p>Reloading...</p>", "text/html; charset=utf-8")
        if path.startswith("/api/") and not self._authed():
            return self._json(401, {"error": "log in"})
        m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/events", path)
        if m:
            return self._events(m.group(1))
        if path == "/api/tvs":
            return self._json(200, [{"mac": m, **t} for m, t in load_tvs().items()])
        if path == "/api/youtube/search":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].strip()[:200]
            if not q:
                return self._json(400, {"error": "type a search"})
            try:
                return self._json(200, youtube.youtube_search(q))
            except (OSError, ValueError, urllib.error.URLError):
                log.exception("YouTube search failed")
                return self._json(502, {"error": "YouTube search did not answer"})
        m = re.fullmatch(r"/api/youtube/thumb/([A-Za-z0-9_-]{11})", path)
        if m:
            try:
                return self._send(200, youtube.youtube_thumb(m.group(1)), "image/jpeg", "private, max-age=86400")
            except (OSError, urllib.error.URLError):
                return self._json(404, {"error": "no thumbnail"})
        m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/(\w+)", path)
        if m and m.group(2) in READS:
            mac, what = m.groups()
            try:
                return self._json(200, READS[what](tvcontrol.control(mac), mac))
            except KeyError:
                return self._json(404, {"error": "unknown TV"})
        name = "index.html" if path == "/" else path.lstrip("/")
        file = (WEB_DIR / name).resolve()
        if file.parent == WEB_DIR.resolve() and file.is_file() and file.suffix in STATIC_TYPES:
            body = file.read_bytes()
            if file.name == "index.html":
                # The version in every asset URL: a new release is a new URL,
                # so no browser can mix an old script with new HTML.
                body = re.sub(rb'(href|src)="([\w-]+\.(?:css|js))"',
                              lambda m: m.group(1) + b'="' + m.group(2) + b"?v=" + VERSION.encode() + b'"', body)
            # A versioned URL never changes; everything else asks each time.
            versioned = "?v=" in self.path and file.suffix in (".js", ".css")
            return self._send(200, body, STATIC_TYPES[file.suffix],
                              "public, max-age=31536000, immutable" if versioned else "no-cache")
        self._json(404, {"error": "not found"})

    def _events(self, mac: str) -> None:
        """The TV's state as it changes (server-sent events)."""
        if mac not in load_tvs():
            return self._json(404, {"error": "unknown TV"})
        if not _streams.acquire(blocking=False):
            return self._json(503, {"error": "too many open pages"})
        life = _live(mac)
        life.watch()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen, state = -1, life.current()
            seen = life.version
            self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
            self.wfile.flush()
            while self._authed():  # logging out ends the stream
                version, state = life.wait(seen, 15)
                # A comment when nothing changed notices a page that has gone.
                line = f"data: {json.dumps(state)}\n\n" if version != seen else ": still here\n\n"
                seen = version
                self.wfile.write(line.encode())
                self.wfile.flush()
        except OSError:
            pass  # the page went away
        finally:
            life.unwatch()
            _streams.release()

    def _voice(self, mac: str) -> None:
        """Audio in; the words, the command, and what it did out."""
        if self.headers.get("X-Remote") != "1" or self.headers.get("Content-Type") != "audio/l16":
            return self._json(403, {"error": "forbidden"})
        # Before reading a byte of the recording.
        if not self._authed():
            return self._json(401, {"error": "log in"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= speech.VOICE_MAX_BYTES or length % 2:
            return self._json(400, {"error": "that recording is too long"})
        pcm = self.rfile.read(length)
        try:
            tv = tvcontrol.control(mac)
        except KeyError:
            return self._json(404, {"error": "unknown TV"})
        refused = speech.admit(self._client())
        if refused:
            speech.duck.restore(mac)
            return self._json(429, {"error": refused})
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        language = q.get("language", ["auto"])[0]
        recent = q.get("recent", ["en"])[0]
        order = [recent] if recent in speech.VOICE_LANGUAGES else []
        order += [x for x in speech.VOICE_LANGUAGES if x not in order]
        try:
            if q.get("mode", [""])[0] == "dictate":
                # Words for a text field: nothing to check them against.
                said = speech.hear(pcm, language if language in speech.VOICE_LANGUAGES else "auto")
                return self._json(200, {"text": said["text"] if speech.heard_speech(said) else "", "language": said["language"]})
            said = speech.understand(pcm, language, order, tv.inputs())
            # Before acting: "volume up" means up from the real level.
            speech.duck.restore(mac)
            out = {"text": said["text"], "language": said.get("language"), "intent": said["intent"]}
            if said["intent"]:
                out.update(run_intent(tv, said["intent"]))
                _live(mac).poke()
            # What was said stays out of the logs: it can be anything.
            log.info("%s: voice (%s, %s ms) -> %s", mac, said.get("language"), said.get("ms"),
                     said["intent"]["type"] if said["intent"] else "no command")
            return self._json(200, out)
        except ConnectionError as err:
            speech.duck.restore(mac)
            return self._json(502, {"error": str(err)})
        except Exception:  # noqa: BLE001 - never leak a traceback to the page
            speech.duck.restore(mac)
            log.exception("voice request failed")
            return self._json(500, {"error": "something went wrong"})
        finally:
            speech.done()

    def _session_cookie(self, token: str) -> str:
        sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"tvr={token}; Max-Age={auth.SESSION_DAYS * 86400}; Path=/; HttpOnly; SameSite=Lax{sec}"

    def _login(self, body: dict) -> None:
        who = self._client()
        if not auth.password_is_set():
            return self._json(503, {"error": "no password set", "setup": True})
        if auth.too_many_tries(who):
            return self._json(429, {"error": "too many tries; wait 10 minutes"})
        if not auth.check_password(str(body.get("password", ""))):
            auth.note_failure(who)
            time.sleep(1)
            return self._json(401, {"error": "wrong password"})
        auth.clear_failures(who)
        return self._json(200, {"ok": True}, self._session_cookie(auth.make_session()))

    # POST: everything that does something
    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/voice", path)
        if m:
            return self._voice(m.group(1))
        try:
            body = self._body()
        except PermissionError:
            return self._json(403, {"error": "forbidden"})
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        if path == "/api/login":
            return self._login(body)
        if path == "/api/logout":
            auth.end_session(self.headers.get("Cookie"))
            sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self._json(200, {"ok": True}, f"tvr=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{sec}")
        if not self._authed():
            return self._json(401, {"error": "log in"})
        if path == "/api/logout-all":
            auth.log_out_everywhere()
            sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self._json(200, {"ok": True}, f"tvr=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{sec}")
        try:
            if path == "/api/discover":
                paired = load_tvs()
                return self._json(200, [{**d, "paired": d["mac"] in paired} for d in samsung.scan()])
            if path == "/api/pair":
                mac, ip = str(body.get("mac", "")).lower(), str(body.get("ip", ""))
                if not samsung.MAC_RE.match(mac) or ipaddress.ip_address(ip) not in TV_SUBNET:
                    return self._json(400, {"error": "bad TV"})
                return self._json(200, {"mac": mac, **samsung.pair(mac, ip)})
            m = re.fullmatch(r"/api/tv/([0-9a-f:]{17})/([\w-]+)", path)
            if not m or m.group(2) not in ACTIONS:
                return self._json(404, {"error": "not found"})
            mac, action = m.groups()
            result = ACTIONS[action](tvcontrol.control(mac), body, mac)
            status, answer = result if result else (200, {"ok": True})
            if action != "forget":
                _live(mac).poke()  # the page should see this at once
            return self._json(status, answer)
        except KeyError:
            return self._json(404, {"error": "unknown TV"})
        except ConnectionError as err:
            return self._json(502, {"error": str(err)})
        except Exception:  # noqa: BLE001 - never leak a traceback to the page
            log.exception("request failed: %s", path)
            return self._json(500, {"error": "something went wrong"})
