"""How the sources ask the web: one request a second at most to each site
(someone else's service), an answer of 4 MB at most, a timeout, readable
errors. Standard library only."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

USER_AGENT = f"stanzarr/{__version__} (+https://github.com/el-amine-404/batlab)"
# Some sites answer only what looks like a browser (Genius's pages).
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 20

_lock = threading.Lock()
_last: dict[str, float] = {}


class Refused(Exception):
    """The site said no in a way worth saying as is (a key refused, a captcha)."""


def _wait(host: str) -> None:
    with _lock:
        pause = 1.0 - (time.monotonic() - _last.get(host, 0.0))
        if pause > 0:
            time.sleep(pause)
        _last[host] = time.monotonic()


def get(url: str, params: dict | None = None, headers: dict | None = None, browser: bool = False,
        method: str = "GET", data: dict | None = None) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={
        "User-Agent": BROWSER if browser else USER_AGENT, "Accept": "*/*", **(headers or {})})
    _wait(urllib.parse.urlsplit(url).netloc)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("the answer is over 4 MB")
    return payload


def get_json(url: str, params: dict | None = None, headers: dict | None = None, **kwargs):
    return json.loads(get(url, params, {"Accept": "application/json", **(headers or {})}, **kwargs))


def explain(error: Exception) -> str:
    """One sentence for a failed question."""
    if isinstance(error, urllib.error.HTTPError):
        return f"it answered {error.code}"
    if isinstance(error, urllib.error.URLError):
        return f"it couldn't be reached ({error.reason})"
    return str(error) or error.__class__.__name__
