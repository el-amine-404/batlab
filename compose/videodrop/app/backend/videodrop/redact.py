"""What a log may say: every line videodrop logs, and every line of a
download's log (journal.py), goes through clean() first.

Secrets never reach a log: a password in an address (user:pass@), a query
parameter that signs or authorises (token=, sig=, key=…), the address the
site was asked from (ip=), a header that carries a credential (Cookie,
Authorization), a notification address (tgram://<bot token>/…; its secrets
are in the address itself). A video site's signed address (hundreds of
characters of query) keeps its host and path only.

A line is one line: a title or an error message from a site can't forge
log lines of its own (new lines are shown as ⏎, terminal escapes and other
control characters are dropped), and is cut at MAX_LINE."""

import re

MAX_LINE = 4000
HIDDEN = "•••"
# Query parameters whose value is a secret, or says too much.
_SECRET_PARAMS = re.compile(
    r"(?i)([?&;](?:[\w.-]*(?:token|secret|passw(?:or)?d|pass|pwd|auth|apikey|api_key|key|sig|signature|session|"
    r"sessionid|sid|credential|cookie)|ip|code|lsig|hash|hmac)=)[^&#\s\"'<>]+")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@\"'<>]+(?::[^/\s@\"'<>]*)?@")
# A name and password before a host, without the address around it (an error message's "bob:pw@host").
_BARE_USERINFO = re.compile(r"(?<![\w/:@.-])[\w.%+-]+:[^\s/@\"'<>]+@(?=[\w-]+(?:\.[\w-]+)*(?::\d+)?\b)")
# Addresses that aren't the web's: notification services (Apprise), whose secrets are their path.
_OTHER_SCHEMES = re.compile(r"(?i)\b(?!(?:https?|ftp|file|rtmp|rtsp)://)([a-z][a-z0-9+.-]{1,20}://)[^\s\"'<>]+")
_HEADERS = re.compile(r"(?i)\b((?:set-)?cookie|authorization|proxy-authorization|x-videodrop-signature|"
                      r"x-api-key)(\s*[:=]\s*)[^\n]+")
_LONG_QUERY = re.compile(r"(\bhttps?://[^\s?#\"'<>]+)\?[^\s#\"'<>]{200,}")
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069]")


def secrets(text: str) -> str:
    """text without the secrets it holds."""
    text = _HEADERS.sub(lambda m: f"{m[1]}{m[2]}{HIDDEN}", text)
    text = _LONG_QUERY.sub(lambda m: f"{m[1]}?{HIDDEN}", text)
    text = _USERINFO.sub(lambda m: f"{m[1]}{HIDDEN}@", text)
    text = _BARE_USERINFO.sub(f"{HIDDEN}@", text)
    text = _OTHER_SCHEMES.sub(lambda m: f"{m[1]}{HIDDEN}", text)
    return _SECRET_PARAMS.sub(lambda m: f"{m[1]}{HIDDEN}", text)


def clean(text: object, limit: int = MAX_LINE) -> str:
    """One line a log can keep: without secrets, escapes or line breaks, at most limit characters."""
    text = _ESCAPES.sub("", str(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").replace("\n", " ⏎ ").replace("\t", " ")
    text = secrets(_CONTROLS.sub("", text))
    return text if len(text) <= limit else f"{text[:limit]}… ({len(text) - limit} more characters)"
