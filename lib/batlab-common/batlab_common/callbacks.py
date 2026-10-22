"""Signed callbacks: the download contract's (docs/services.md, "The download
contract"). A service that tells a caller about a job signs the body with the
caller's secret, so the caller can tell the call came from the service:

    X-<App>-Timestamp: <unix seconds>
    X-<App>-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<body>", the secret as key>

Standard library only: songdrop calls it from spotDL's environment too.
"""

import hashlib
import hmac
import time

# A signed call older than this is refused, so a captured one can't be replayed.
MAX_AGE = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def headers(app: str, secret: str | None, body: bytes, event: str) -> dict[str, str]:
    """The headers of one call: its event and time, and its signature when the
    caller gave a secret."""
    name = app.capitalize()
    timestamp = str(int(time.time()))
    out = {f"X-{name}-Event": event, f"X-{name}-Timestamp": timestamp}
    if secret:
        out[f"X-{name}-Signature"] = sign(secret, timestamp, body)
    return out


def verify(secret: str, timestamp: str | None, body: bytes, signature: str | None, now: float | None = None) -> bool:
    """Whether a call was signed with secret, within MAX_AGE."""
    if not (timestamp and signature and timestamp.isdigit()):
        return False
    if abs((now or time.time()) - int(timestamp)) > MAX_AGE:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)
