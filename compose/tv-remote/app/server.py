"""Web remote for Samsung Tizen TVs on the LAN. Where things live: ../README.md."""

from __future__ import annotations

import getpass
import logging
import os
import sys
from http.server import ThreadingHTTPServer

import auth
from config import DATA_DIR, LISTEN_ADDR, PORT, TOKENS_DIR, TV_SUBNET, VERSION
from http_api import Handler

log = logging.getLogger("tv-remote")


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
    server = ThreadingHTTPServer((LISTEN_ADDR, PORT), Handler)
    server.daemon_threads = True
    log.info("tv-remote %s listening on %s:%d, scanning %s", VERSION, LISTEN_ADDR, PORT, TV_SUBNET)
    server.serve_forever()


if __name__ == "__main__":
    main()
