"""What differs from one TV to another: for now, its inputs (profiles.json)."""

from __future__ import annotations

import json
from pathlib import Path

_PROFILES = json.loads(Path(__file__).with_name("profiles.json").read_text(encoding="utf-8"))


def profile_for(mac: str = "", model: str = "") -> dict:
    return _PROFILES.get(mac) or _PROFILES.get(model) or _PROFILES["default"]
