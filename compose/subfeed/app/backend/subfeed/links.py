"""What a post links to, read from its link and its text: the videos it
carries, and the kind of its link. A new video provider is one line in
PROVIDERS."""

import html
import re
from urllib.parse import urlsplit

from .domain import Video

# provider: (a video's id in any link to it, the address of a video).
# YouTube Shorts are clips, not videos: left out.
PROVIDERS: dict[str, tuple[re.Pattern, str]] = {
    "youtube": (
        re.compile(r"(?:youtube\.com/(?:watch\?(?:[^\"'\s<>]*?&(?:amp;)?)?v=|embed/|live/)|youtu\.be/)"
                   r"([\w-]{11})(?![\w-])"),
        "https://www.youtube.com/watch?v={id}",
    ),
}
_IMAGE = re.compile(r"(?i)\.(?:jpe?g|png|gif|gifv|webp)(?:\?|$)")
_IMAGE_HOSTS = ("i.redd.it", "i.imgur.com", "preview.redd.it")


def videos_in(text: str | None) -> list[Video]:
    """The videos a text links to, once each, in order, provider by provider."""
    text = html.unescape(text or "")
    found: list[Video] = []
    for provider, (pattern, _url) in PROVIDERS.items():
        found += [Video(provider, video_id) for video_id in dict.fromkeys(pattern.findall(text))]
    return found


def video_url(video: Video) -> str:
    return PROVIDERS[video.provider][1].format(id=video.id)


def domain_of(link: str | None) -> str | None:
    return (urlsplit(link).netloc.casefold().removeprefix("www.") or None) if link else None


def kind_of(link: str | None, videos: list[Video]) -> str:
    """video when the post carries a video, else what its link is."""
    if videos:
        return "video"
    if not link:
        return "text"
    host = domain_of(link) or ""
    if host == "v.redd.it":
        return "reddit_video"
    if host in _IMAGE_HOSTS or _IMAGE.search(link):
        return "image"
    if host.endswith("reddit.com") and "/gallery/" in link:
        return "gallery"
    return "other"
