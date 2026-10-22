"""Requests songdrop makes to other services: connections (connections/) and
callback_url. One place for timeouts, the User-Agent and readable errors."""

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from batlab_common import callbacks

log = logging.getLogger("songdrop.outbound")

TIMEOUT_SECONDS = 15
USER_AGENT = "songdrop"


class SendFailed(Exception):
    """The message is shown to the user as is."""


def post_json(url: str, payload: dict, secret: str | None = None) -> None:
    """POSTs payload as JSON; signed with secret when there is one (a job's
    callback_secret, batlab_common.callbacks)."""
    # urllib opens file:// and ftp:// too: only the web, whatever let the address in.
    if not url.startswith(("http://", "https://")):
        raise SendFailed("Only http:// and https:// addresses are sent to.")
    body = json.dumps(payload, ensure_ascii=False, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
               **callbacks.headers("songdrop", secret, body, payload.get("event", "message"))}
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    host = urllib.parse.urlsplit(url).hostname or url
    try:
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS).close()
    except urllib.error.HTTPError as exc:
        raise SendFailed(f"{host} answered {exc.code} ({exc.reason}).") from exc
    except urllib.error.URLError as exc:
        raise SendFailed(f"Couldn't reach {host}: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise SendFailed(f"{host} didn't answer within {TIMEOUT_SECONDS} s.") from exc


def in_background(name: str, send, *args) -> None:
    """Runs send(*args) on its own thread: a slow or unreachable service never
    holds a job up. A failure is only logged."""

    def run() -> None:
        try:
            send(*args)
        except SendFailed as exc:
            log.warning("%s failed: %s", name, exc)
        except Exception:
            log.exception("%s failed", name)

    threading.Thread(target=run, name=name, daemon=True).start()
