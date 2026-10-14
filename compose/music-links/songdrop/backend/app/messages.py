"""Engine errors turned into a sentence a person can act on."""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# "ERROR: [youtube] dQw4w9WgXcQ: Video unavailable" -> "Video unavailable"
_YTDLP_PREFIX = re.compile(r"^(?:ERROR:\s*)?(?:\[[^\]]+\]\s*)?(?:[\w-]{6,}:\s+)?")
_CLASS_PREFIX = re.compile(r"^(?:[A-Z][A-Za-z]+(?:Error|Exception)):\s*")

_KNOWN = [
    (re.compile(r"sign in to confirm you.?re not a bot", re.I),
     "YouTube asked to confirm this isn't a bot. Try again in a while."),
    (re.compile(r"HTTP Error 403|403: Forbidden", re.I),
     "The site refused the download (403). yt-dlp may need an update; the nightly updater installs new ones."),
    (re.compile(r"HTTP Error 429|Too Many Requests", re.I),
     "The site is rate-limiting downloads. Try again later."),
    (re.compile(r"YT-DLP download error", re.I),
     "YouTube refused the audio three times in a row. Try this one again later."),
    (re.compile(r"No results found for song", re.I),
     "No matching song was found to download."),
    (re.compile(r"Private video", re.I), "This video is private."),
    (re.compile(r"members[- ]only", re.I), "This video is for channel members only."),
    (re.compile(r"age[- ]restricted|confirm your age", re.I), "This video is age-restricted and needs a signed-in account."),
    (re.compile(r"not available in your country|geo.?restrict", re.I), "This isn't available from this country."),
    (re.compile(r"Video unavailable", re.I), "This video is unavailable."),
    (re.compile(r"Unsupported URL", re.I), "yt-dlp doesn't support this link."),
    (re.compile(r"timed out|Read timed out|ConnectTimeout", re.I), "The site took too long to answer. Try again."),
]

MAX_LENGTH = 300


def friendly(raw: str | None) -> str:
    text = _ANSI.sub("", raw or "").strip()
    if not text:
        return "Something went wrong."
    for pattern, message in _KNOWN:
        if pattern.search(text):
            return message
    first = text.splitlines()[0]
    first = _CLASS_PREFIX.sub("", _YTDLP_PREFIX.sub("", first)).strip()
    if len(first) > MAX_LENGTH:
        first = first[: MAX_LENGTH - 1].rstrip() + "…"
    return first or "Something went wrong."
