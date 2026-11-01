"""Settings from the environment, in one place."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
TV_SUBNET = ipaddress.ip_network(os.environ.get("TV_SUBNET", "192.168.1.0/24"), strict=False)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
REMOTE_NAME = os.environ.get("REMOTE_NAME", "batlab remote")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
SPEECH_URL = os.environ.get("SPEECH_URL", "").rstrip("/")
WEB_DIR = Path(__file__).with_name("web")
VERSION = Path(__file__).with_name("VERSION").read_text().strip()

TVS_FILE = DATA_DIR / "tvs.json"
TOKENS_DIR = DATA_DIR / "tokens"
AUTH_FILE = DATA_DIR / "auth.json"
