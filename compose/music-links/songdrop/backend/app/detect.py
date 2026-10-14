"""Which engine takes a link: spotDL for Spotify, yt-dlp for any site it knows."""

import re
import threading
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlsplit

MAX_URL_LENGTH = 2048

_SPOTIFY_WEB = re.compile(
    r"^/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(?:embed/)?"
    r"(?P<kind>track|album|playlist|artist)/(?P<id>[A-Za-z0-9]{22})/?$"
)
_SPOTIFY_URI = re.compile(r"^spotify:(?P<kind>track|album|playlist|artist):(?P<id>[A-Za-z0-9]{22})$")

# Friendlier names than yt-dlp's extractor keys for the sites people paste.
_SITE_NAMES = {
    "music.youtube.com": "YouTube Music",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "soundcloud.com": "SoundCloud",
    "bandcamp.com": "Bandcamp",
    "vimeo.com": "Vimeo",
    "deezer.com": "Deezer",
    "mixcloud.com": "Mixcloud",
    "audiomack.com": "Audiomack",
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "x.com": "X",
    "twitter.com": "X",
    "facebook.com": "Facebook",
}


class UnsupportedLink(ValueError):
    """The message is shown to the user as is."""


@dataclass(frozen=True)
class Detection:
    engine: str  # "spotdl" or "ytdlp"
    site: str
    kind: str  # track, album, playlist, artist (Spotify) or media, playlist
    url: str
    # A video link that also names a playlist (YouTube's watch?v=…&list=…).
    has_playlist: bool = False

    def as_dict(self) -> dict:
        data = asdict(self)
        data["engine_label"] = "spotDL" if self.engine == "spotdl" else "yt-dlp"
        return data


_extractors = None
_extractors_lock = threading.Lock()


def _ytdlp_extractors():
    """yt-dlp's extractors minus Generic, which would accept any web page."""
    global _extractors
    with _extractors_lock:
        if _extractors is None:
            from yt_dlp.extractor import gen_extractor_classes

            _extractors = [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]
        return _extractors


def warm_up() -> None:
    """Loads and compiles the extractor list, which takes a second or two."""
    _ytdlp_site("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def _ytdlp_site(url: str):
    for extractor in _ytdlp_extractors():
        try:
            if extractor.suitable(url):
                return extractor
        except Exception:  # a broken extractor regex must not break detection
            continue
    return None


def _site_name(host: str, extractor) -> str:
    host = host.lower().removeprefix("www.").removeprefix("m.")
    for domain, name in _SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return extractor.IE_NAME.split(":")[0].replace("_", " ").title()


def detect(raw: str) -> Detection:
    url = (raw or "").strip()
    if not url:
        raise UnsupportedLink("Paste a link first.")
    if len(url) > MAX_URL_LENGTH:
        raise UnsupportedLink("That link is too long.")
    if any(ch.isspace() for ch in url):
        raise UnsupportedLink("Paste one link at a time.")

    match = _SPOTIFY_URI.match(url)
    if match:
        kind, ident = match["kind"], match["id"]
        return Detection("spotdl", "Spotify", kind, f"https://open.spotify.com/{kind}/{ident}")

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsupportedLink("That doesn't look like a web link (it should start with https://).")
    host = parts.hostname.lower()

    if host in ("open.spotify.com", "play.spotify.com"):
        match = _SPOTIFY_WEB.match(parts.path)
        if not match:
            raise UnsupportedLink("Use a Spotify track, album, playlist or artist link.")
        kind, ident = match["kind"], match["id"]
        # Query strings (?si=…) are tracking only; the canonical link is enough.
        return Detection("spotdl", "Spotify", kind, f"https://open.spotify.com/{kind}/{ident}")
    if host == "spotify.link" or host.endswith(".spotify.link") or host.endswith("spotify.app.link"):
        raise UnsupportedLink("Short spotify.link links can't be read. Open it, then copy the open.spotify.com link.")
    if host.endswith("spotify.com"):
        raise UnsupportedLink("Use a Spotify track, album, playlist or artist link.")

    extractor = _ytdlp_site(url)
    if extractor is None:
        raise UnsupportedLink("yt-dlp doesn't know this site, so it can't be downloaded here.")

    name = extractor.IE_NAME.lower()
    query = parse_qs(parts.query)
    is_list = any(word in name for word in ("playlist", "album", "tab", "set", "list", "user", "channel"))
    has_playlist = "list" in query and "v" in query
    kind = "playlist" if is_list and not has_playlist else "media"
    return Detection("ytdlp", _site_name(host, extractor), kind, url, has_playlist)
