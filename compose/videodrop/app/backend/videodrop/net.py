"""What an address is called on the home network, for people reading
"used 5 min ago by homepage": Docker's DNS names the apps' containers
(172.19.10.30 → homepage.home_server), the router or AdGuard names devices.
A lookup waits a second at most and is kept an hour; no name, None."""

import ipaddress
import socket
import threading
import time

KEEP = 3600
WAIT = 1.0
_known: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


def name_of(address: str | None, resolve=socket.gethostbyaddr) -> str | None:
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.is_loopback:
        return "videodrop itself"
    with _lock:
        found = _known.get(address)
    if found and found[0] > time.time():
        return found[1]
    answer: list[str] = []

    def look() -> None:
        try:
            answer.append(resolve(address)[0])
        except OSError:
            pass

    worker = threading.Thread(target=look, daemon=True)
    worker.start()
    worker.join(WAIT)
    name = answer[0] if answer else None
    # On the home network, its first part: "homepage", not "homepage.home_server"; "pixel-7", not "pixel-7.lan".
    if name and ip.is_private:
        name = name.split(".", 1)[0]
    with _lock:
        _known[address] = (time.time() + KEEP, name)
    return name
