"""The paired TVs, kept in DATA_DIR."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from config import TOKENS_DIR, TVS_FILE

store_lock = threading.Lock()


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
