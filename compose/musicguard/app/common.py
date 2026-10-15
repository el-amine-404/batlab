"""What every part of the guard uses: its two verdicts that are not
failures, the time and the log."""

import os
from datetime import datetime, timezone


class Hold(Exception):
    """Not safe to decide now (clamd down); leave the file and try later."""


class Reject(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message):
    print(f"{now_iso()} {message}", flush=True)


def name_is_hidden(path):
    return os.path.basename(path).startswith(".")
