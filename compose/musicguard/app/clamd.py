"""The host's clamd, over its socket, by streaming (INSTREAM).

Streaming rather than passing a path or descriptor: clamd runs outside this
container and could not open its paths (mediascan/README.md has the same
finding for its sandbox). clamd's StreamMaxLength (25M on lab2) bounds what it
accepts, so larger files are reported as not scanned rather than clean.
"""

import socket
import struct

CHUNK = 64 * 1024
# The standard antivirus test string; clamd must detect it or it is not working.
EICAR = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")


class ClamdError(Exception):
    pass


def _scan(socket_path, chunks, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(socket_path)
        conn.sendall(b"zINSTREAM\0")
        for chunk in chunks:
            conn.sendall(struct.pack("!L", len(chunk)) + chunk)
        conn.sendall(struct.pack("!L", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            data = conn.recv(4096)
            if not data:
                break
            reply += data
    return reply.rstrip(b"\0").decode(errors="replace")


def parse(reply):
    """(infected, signature) from clamd's reply; raises on errors."""
    if reply.endswith(" OK"):
        return False, ""
    if reply.endswith(" FOUND"):
        return True, reply.split(":", 1)[-1].rsplit(" ", 1)[0].strip()
    raise ClamdError(reply or "empty reply")


def scan_file(socket_path, path, timeout=120):
    def chunks():
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    return
                yield chunk
    return parse(_scan(socket_path, chunks(), timeout))


def self_test(socket_path):
    """Raise unless clamd answers and flags the EICAR string."""
    infected, _ = parse(_scan(socket_path, [EICAR], 30))
    if not infected:
        raise ClamdError("clamd did not detect the EICAR test string")
