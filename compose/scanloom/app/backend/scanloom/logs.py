"""Logging infrastructure for Scanloom.

Features:
- Standard stream logging to sys.stderr with UTC timestamps (visible in docker logs and Dozzle).
- Self-rotating file handler (logging.handlers.RotatingFileHandler) with bounded size and backup count.
- In-memory ring buffer of recent logs for query via the API (GET /api/v2/system/logs).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

KEPT = 1000
_LEVELS = {"DEBUG": "debug", "INFO": "info", "WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}


class Recent(logging.Handler):
    """Ring buffer holding the most recent records in memory."""

    def __init__(self, maxlen: int = KEPT):
        super().__init__(logging.INFO)
        self._lines: deque[dict] = deque(maxlen=maxlen)
        self._seq = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._guard:
                self._seq += 1
                detail = None
                if record.exc_info:
                    detail = logging.Formatter().formatException(record.exc_info)
                self._lines.append({
                    "seq": self._seq,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                    "level": _LEVELS.get(record.levelname, "info"),
                    "logger": record.name,
                    "message": record.getMessage(),
                    "detail": detail,
                })
        except Exception:
            self.handleError(record)

    def lines(self, after: int = 0, level: str | None = None, limit: int = 200) -> list[dict]:
        order = ("debug", "info", "warning", "error")
        least = order.index(level) if level in order else 0
        with self._guard:
            found = [line for line in self._lines if line["seq"] > after and order.index(line["level"]) >= least]
        return found[-limit:]


RECENT = Recent()
_CONFIGURED = False


def setup(component: str = "scanloom", logs_dir: Path | None = None, level: int = logging.INFO) -> None:
    """Initialize structured stderr logging, self-rotating log files, and in-memory buffer."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)-7s [%(name)s] %(message)s",
        "%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime

    # 1. Stderr handler for docker logs / container output
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    root.addHandler(stderr)

    # 2. In-memory recent logs handler
    root.addHandler(RECENT)

    # 3. Self-rotating file handler
    if logs_dir is not None:
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = logs_dir / f"{component}.log"
            file_handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5,
                encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            logging.getLogger("scanloom").info("Self-rotating log file initialized at %s", log_file)
        except Exception as error:
            try:
                fallback_dir = Path("/tmp/scanloom-logs")
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_file = fallback_dir / f"{component}.log"
                file_handler = RotatingFileHandler(
                    filename=str(fallback_file),
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                root.addHandler(file_handler)
                logging.getLogger("scanloom").warning(
                    "Could not write to %s (%s); writing logs to fallback %s",
                    logs_dir, error, fallback_file
                )
            except Exception:
                logging.getLogger("scanloom").warning("Could not initialize file logger: %s", error)

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
