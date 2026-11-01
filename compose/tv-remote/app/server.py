"""Web remote for Samsung Tizen TVs on the LAN. Where things live: ../README.md."""

from __future__ import annotations

import getpass
import logging
import os
import sys
from http.server import ThreadingHTTPServer

import auth
import powerlog
import samsung
import sleeptimer
import tvcontrol
from config import DATA_DIR, LISTEN_ADDR, PORT, TOKENS_DIR, TV_SUBNET, VERSION
from http_api import Handler
from store import load_tvs

log = logging.getLogger("tv-remote")


def _turn_off(mac: str) -> None:
    tv = tvcontrol.control(mac)
    if tv.status().get("power") == "on":
        tv.toggle_power()


def _power_reading(mac: str) -> str:
    dev = samsung.device_info(load_tvs()[mac]["ip"], timeout=1.5)
    return dev["power"] if dev else "off"


def main() -> None:
    if sys.argv[1:] == ["set-password"]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        p1 = getpass.getpass("New password for the TV remote: ")
        p2 = getpass.getpass("Again: ")
        if p1 != p2 or len(p1) < 10:
            sys.exit("the passwords differ, or are under 10 characters; nothing changed")
        auth.set_password(p1)
        print("password set; every device must log in again")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKENS_DIR, 0o700)
    sleeptimer.start(_turn_off)
    powerlog.start(load_tvs, _power_reading)
    server = ThreadingHTTPServer((LISTEN_ADDR, PORT), Handler)
    server.daemon_threads = True
    log.info("tv-remote %s listening on %s:%d, scanning %s", VERSION, LISTEN_ADDR, PORT, TV_SUBNET)
    server.serve_forever()


if __name__ == "__main__":
    main()
