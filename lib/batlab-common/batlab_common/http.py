"""GET with a size limit and a pause between requests to one host, so a
batch of lookups never hammers a free API."""

import threading
import time
import urllib.parse
import urllib.request

AGENT = "batlab/1.0 (self-hosted music library; +https://github.com/el-amine-404/batlab)"

_lock = threading.Lock()
_last = {}  # host -> time.monotonic() of the last request


class TooLarge(ValueError):
    """The answer was longer than the limit."""


def get(url, limit, spacing=0.0, timeout=30, agent=AGENT):
    """The body of url, at most limit bytes. spacing: seconds to wait since
    the last request to the same host."""
    host = urllib.parse.urlsplit(url).netloc
    if spacing:
        with _lock:
            wait = spacing - (time.monotonic() - _last.get(host, 0.0))
            _last[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise TooLarge(f"{host} answered more than {limit} bytes")
    return body
