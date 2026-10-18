"""videodrop's own log: to stderr (the container's log), and its newest
lines kept in memory for the page (Settings, Log), each with a number to
follow it from.

Every record goes through redact.clean (its message; a traceback keeps its
lines but not its secrets): a log can be shared to find a problem without
giving a password away."""

import logging
import threading
import time
from collections import deque

from .redact import clean, secrets

KEPT = 2000
_LEVELS = {"DEBUG": "debug", "INFO": "info", "WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}


class Clean(logging.Filter):
    """The message, made once and cleaned; the traceback without its secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = clean(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = secrets(logging.Formatter().formatException(record.exc_info))
        return True


class Recent(logging.Handler):
    """The newest KEPT records, numbered."""

    def __init__(self):
        super().__init__(logging.INFO)
        self._lines: deque[dict] = deque(maxlen=KEPT)
        self._seq = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._guard:
            self._seq += 1
            self._lines.append({"seq": self._seq, "at": record.created, "level": _LEVELS.get(record.levelname, "info"),
                                "source": record.name, "message": record.getMessage(),
                                "detail": record.exc_text or None})

    def lines(self, after: int = 0, level: str | None = None, limit: int = 500) -> list[dict]:
        order = ("debug", "info", "warning", "error")
        least = order.index(level) if level in order else 0
        with self._guard:
            found = [line for line in self._lines if line["seq"] > after and order.index(line["level"]) >= least]
        return found[-limit:]


RECENT = Recent()


def setup(level: int = logging.INFO) -> None:
    """Once, at the start: every logger's records cleaned, to stderr and to RECENT."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    for handler in (stderr, RECENT):
        handler.addFilter(Clean())
        root.addHandler(handler)
    # One line per outgoing request is noise; failures are logged where they matter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
