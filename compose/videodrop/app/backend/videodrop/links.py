"""The links videodrop takes: a site yt-dlp has an extractor for (not its
generic one, which would fetch any address, the home network's included),
over http or https. The site's name groups downloads for the per-site limit
(YouTube answers everyone at this address as one)."""

import functools
import ipaddress
import re
import urllib.parse

from batlab_common import egress

from .domain import Invalid

_YOUTUBE_ID = re.compile(r"^[\w-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
                  "www.youtube-nocookie.com", "youtube-nocookie.com"}
# Hosts that are one site for the per-site limit.
_SITE_ALIASES = {"youtu.be": "youtube.com", "youtube-nocookie.com": "youtube.com"}


def _host_allowed(host: str) -> bool:
    """Not an address on this network: no IP of a private range, no single
    name (a container's, like apprise)."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return "." in host and not host.endswith((".lan", ".local", ".internal", ".localhost", ".home.arpa"))
    return address.is_global


# How a link's host is looked up (egress.resolve); the tests give their own.
RESOLVE = egress.resolve


async def reachable(url: str) -> None:
    """Refuses a link whose host points inside this network now (Invalid),
    so that it is said at once; the egress proxy refuses it again, by the
    address it connects to, whatever the name says by then. A name that
    doesn't resolve now isn't refused: the download says so if it still
    doesn't."""
    parts = urllib.parse.urlsplit(url)
    try:
        await RESOLVE(parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))
    except egress.Refused as exc:
        raise Invalid(f"videodrop downloads from the internet, not from addresses on this network: {exc}.") from None
    except (ConnectionError, OSError, ValueError):
        pass


def site_of(url: str) -> str:
    """youtube.com for www.youtube.com, m.youtube.com, youtu.be…"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    return _SITE_ALIASES.get(host, host)


def canonical(url: str) -> str:
    """One spelling per video, for knowing a download is asked twice: a
    YouTube video is https://www.youtube.com/watch?v=<id>[&list=…]; any
    other link loses only its #fragment."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        query = urllib.parse.parse_qs(parts.query)
        video = parts.path.strip("/") if host == "youtu.be" else (query.get("v") or [""])[0]
        if not video and parts.path.startswith(("/shorts/", "/live/", "/embed/")):
            video = parts.path.split("/")[2]
        keep = {"v": video} if _YOUTUBE_ID.match(video or "") else {}
        if query.get("list"):
            keep["list"] = query["list"][0]
        if keep:
            path = "/watch" if "v" in keep else "/playlist"
            return urllib.parse.urlunsplit(("https", "www.youtube.com", path, urllib.parse.urlencode(keep), ""))
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


@functools.cache
def _extractors():
    # yt-dlp's list of sites, once (a second the first time).
    from yt_dlp.extractor import gen_extractor_classes

    return [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic" and ie._ENABLED]


def supported(url: str) -> bool:
    return any(ie.suitable(url) for ie in _extractors())


def check(url: str) -> str:
    """The link, trimmed; raises Invalid with a sentence."""
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Invalid("Give a link starting with http:// or https://.")
    if parts.username or parts.password:
        raise Invalid("A link with a user name or password in it isn't taken.")
    if not _host_allowed(parts.hostname.lower()):
        raise Invalid("videodrop downloads from the internet, not from addresses on this network.")
    if not supported(url):
        raise Invalid(f"videodrop can't download from {site_of(url) or 'this site'}: yt-dlp doesn't know it.")
    return url
