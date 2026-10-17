"""Where posts come from, without an account or a key.

- Reddit's own Atom feed of a subreddit's newest posts (/r/<name>/new/.rss):
  fresh to the minute, the last 100 posts. Reddit refuses its JSON without a
  login, and answers 429 to an address that asks too often: one call at a
  time, at least SPACING seconds apart.
- PullPush (api.pullpush.io), an archive of Reddit: the history, page by
  page, a few days behind. A new feed is filled from it, and it stands in
  when Reddit refuses.
"""

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
SPACING = 10.0
ATOM = "{http://www.w3.org/2005/Atom}"
# A video's id in any link to it; Shorts are clips, not music videos.
YOUTUBE = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^\"'\s<>]*?&(?:amp;)?)?v=|embed/|live/)|youtu\.be/)([\w-]{11})(?![\w-])")


# What a post links to (Post.kind), for readers to filter on.
KINDS = ("youtube", "reddit_video", "image", "gallery", "text", "other")
_IMAGE = re.compile(r"(?i)\.(?:jpe?g|png|gif|gifv|webp)(?:\?|$)")
_ANCHOR = re.compile(r'<a href="([^"]+)">\[link\]</a>')


def link_kind(link: str | None, videos: list[str]) -> str:
    """youtube when the post carries a YouTube video, else what its link is."""
    if videos:
        return "youtube"
    if not link:
        return "text"
    host = urlsplit(link).netloc.casefold().removeprefix("www.")
    if host == "v.redd.it":
        return "reddit_video"
    if host in ("i.redd.it", "i.imgur.com", "preview.redd.it") or _IMAGE.search(link):
        return "image"
    if host.endswith("reddit.com") and "/gallery/" in link:
        return "gallery"
    return "other"


def domain_of(link: str | None) -> str | None:
    return urlsplit(link).netloc.casefold().removeprefix("www.") or None if link else None


class Refused(Exception):
    """The source said no (a limit, a block): the message says which."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Post:
    id: str
    title: str
    url: str
    author: str | None
    posted_at: float
    videos: list[str] = field(default_factory=list)
    # What it links to; None for a text post.
    link: str | None = None

    @property
    def kind(self) -> str:
        return link_kind(self.link, self.videos)


def youtube_ids(text: str) -> list[str]:
    """The videos a text links to, once each, in order."""
    return list(dict.fromkeys(YOUTUBE.findall(html.unescape(text or ""))))


def _iso(value: str) -> float:
    # "2026-10-17T08:48:16+00:00"
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def parse_atom(body: str) -> list[Post]:
    root = ET.fromstring(body)
    posts = []
    for entry in root.iter(f"{ATOM}entry"):
        post_id = (entry.findtext(f"{ATOM}id") or "").removeprefix("t3_")
        link = entry.find(f"{ATOM}link")
        published = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        if not post_id or link is None or not published:
            continue
        author = entry.findtext(f"{ATOM}author/{ATOM}name")
        content = entry.findtext(f"{ATOM}content") or ""
        permalink = link.get("href", "")
        # "[link]" is what the post links to; a text post's links to itself.
        target = _ANCHOR.search(content)
        target = html.unescape(target.group(1)) if target else None
        posts.append(Post(id=post_id, title=html.unescape(entry.findtext(f"{ATOM}title") or "").strip(),
                          url=permalink, author=(author or "").removeprefix("/u/") or None,
                          posted_at=_iso(published), videos=youtube_ids(content),
                          link=None if not target or target.rstrip("/") == permalink.rstrip("/") else target))
    return posts


def parse_pullpush(data: dict) -> list[Post]:
    posts = []
    for item in data.get("data") or []:
        if not item.get("id") or not item.get("created_utc"):
            continue
        links = " ".join(str(item.get(key) or "") for key in ("url", "url_overridden_by_dest", "selftext"))
        permalink = item.get("permalink") or f"/comments/{item['id']}/"
        target = item.get("url_overridden_by_dest") or item.get("url")
        posts.append(Post(id=item["id"], title=html.unescape(item.get("title") or "").strip(),
                          url=f"https://www.reddit.com{permalink}", author=item.get("author"),
                          posted_at=float(item["created_utc"]), videos=youtube_ids(links),
                          link=None if item.get("is_self") or not target or "/comments/" in target else target))
    return posts


class Reddit:
    def __init__(self, http: httpx.AsyncClient, user_agent: str):
        self._http = http
        self._headers = {"User-Agent": user_agent}
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        async with self._lock:
            wait = self._last + SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._http.get(url, params=params, headers=self._headers, timeout=TIMEOUT,
                                                follow_redirects=True)
            finally:
                self._last = time.monotonic()
        if response.status_code in (403, 429):
            retry = response.headers.get("retry-after")
            raise Refused(f"{response.url.host} answered {response.status_code}",
                          float(retry) if retry and retry.isdigit() else None)
        response.raise_for_status()
        return response

    async def newest(self, subreddit: str) -> list[Post]:
        """The subreddit's last 100 posts, from Reddit itself."""
        response = await self._get(f"https://www.reddit.com/r/{subreddit}/new/.rss", {"limit": 100})
        return parse_atom(response.text)

    async def archive(self, subreddit: str, before: float | None = None) -> list[Post]:
        """100 posts from PullPush, newest first, older than before."""
        params = {"subreddit": subreddit, "size": 100, "sort": "desc", "sort_type": "created_utc"}
        if before:
            params["before"] = int(before)
        response = await self._get("https://api.pullpush.io/reddit/search/submission/", params)
        return parse_pullpush(response.json())
