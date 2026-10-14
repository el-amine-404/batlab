"""Discord messages about songs: what is missing after a job, what a later
try brought back, and what songdrop gave up on. Sent in the background: a
slow or unreachable Discord never holds a job up."""

import json
import logging
import threading
import urllib.request

log = logging.getLogger("songdrop.notify")


def send(webhook: str | None, lines: list[str]) -> None:
    if not webhook or not lines:
        return
    body = json.dumps({"content": "\n".join(lines)[:1900], "allowed_mentions": {"parse": []}}).encode()

    def post() -> None:
        request = urllib.request.Request(webhook, data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "batlab-songdrop"})
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except Exception as exc:
            log.warning("discord notification failed: %s", exc)

    threading.Thread(target=post, name="notify", daemon=True).start()


def minutes(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    hours = seconds / 3600
    return f"{hours:.0f} h" if hours >= 2 else f"{hours:.1f} h"
